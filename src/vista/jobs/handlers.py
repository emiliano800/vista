import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from vista.config import settings
from vista.db import tenant_session
from vista.models.platform import Job
from vista.models.tenant import AgentRun, AgentRunEvent, Document, UsageEvent

# USD per token: (input, output). Extend as models are adopted.
MODEL_PRICING = {
    "gpt-4o-mini": (Decimal("0.00000015"), Decimal("0.00000060")),
    "gpt-4o": (Decimal("0.0000025"), Decimal("0.00001")),
}
DEFAULT_PRICING = (Decimal("0.000003"), Decimal("0.000015"))


def _pricing(model: str) -> tuple[Decimal, Decimal]:
    for prefix, prices in sorted(MODEL_PRICING.items(), key=lambda kv: -len(kv[0])):
        if model.startswith(prefix):
            return prices
    return DEFAULT_PRICING


def _call_model(document: Document | None) -> tuple[str, str, int, int]:
    """Returns (model, output_text, input_tokens, output_tokens). Uses OpenAI when
    a key is configured, otherwise a deterministic stub."""
    if settings.openai_api_key:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        subject = (
            f"a document named {document.filename!r}" if document else "a deal with no document"
        )
        resp = client.chat.completions.create(
            model=settings.openai_model,
            max_tokens=200,
            messages=[
                {
                    "role": "system",
                    "content": "You are Vista, an analyst agent for private-equity deal teams.",
                },
                {
                    "role": "user",
                    "content": f"In 2-3 sentences, describe what analysis you would run on {subject} during diligence.",
                },
            ],
        )
        return (
            resp.model,
            resp.choices[0].message.content or "",
            resp.usage.prompt_tokens,
            resp.usage.completion_tokens,
        )
    return "stub-model-v0", "stub analysis complete", 1200, 300


def _emit(session, run_id: uuid.UUID, seq: int, event_type: str, data: dict) -> int:
    session.add(AgentRunEvent(run_id=run_id, seq=seq, event_type=event_type, data=data))
    return seq + 1


def handle_agent_run(job: Job, tenant_schema: str) -> None:
    """Stub agent: 'reads' the target document and emits a summary event.
    Replace the model_call section with a real LLM call later."""
    run_id = uuid.UUID(job.payload["run_id"])
    with tenant_session(tenant_schema) as session:
        run = session.get(AgentRun, run_id)
        if run is None:
            raise RuntimeError(f"agent run {run_id} not found in {tenant_schema}")
        run.status = "running"
        seq = 1 + (
            session.scalar(
                select(AgentRunEvent.seq)
                .where(AgentRunEvent.run_id == run_id)
                .order_by(AgentRunEvent.seq.desc())
                .limit(1)
            )
            or 0
        )
        seq = _emit(session, run_id, seq, "step", {"message": "run started"})

        document = session.get(Document, run.document_id) if run.document_id else None
        if document is not None:
            seq = _emit(
                session, run_id, seq, "tool_call",
                {"tool": "read_document", "s3_key": document.s3_key, "filename": document.filename},
            )

        model, output_text, input_tokens, output_tokens = _call_model(document)
        in_price, out_price = _pricing(model)
        cost = Decimal(input_tokens) * in_price + Decimal(output_tokens) * out_price
        seq = _emit(
            session, run_id, seq, "model_call",
            {"model": model, "input_tokens": input_tokens, "output_tokens": output_tokens},
        )
        session.add(
            UsageEvent(
                run_id=run_id,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
            )
        )

        seq = _emit(session, run_id, seq, "result", {"summary": output_text})
        run.status = "succeeded"
        run.finished_at = datetime.now(timezone.utc)
        session.commit()


def mark_run_failed(job: Job, tenant_schema: str, error: str, permanent: bool) -> None:
    run_id = uuid.UUID(job.payload["run_id"])
    with tenant_session(tenant_schema) as session:
        run = session.get(AgentRun, run_id)
        if run is None:
            return
        seq = 1 + (
            session.scalar(
                select(AgentRunEvent.seq)
                .where(AgentRunEvent.run_id == run_id)
                .order_by(AgentRunEvent.seq.desc())
                .limit(1)
            )
            or 0
        )
        _emit(session, run_id, seq, "error", {"error": error[:2000], "permanent": permanent})
        if permanent:
            run.status = "failed"
            run.finished_at = datetime.now(timezone.utc)
        else:
            run.status = "queued"
        session.commit()


HANDLERS = {"agent_run": handle_agent_run}
