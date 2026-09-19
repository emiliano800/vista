import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from vista.db import tenant_session
from vista.models.platform import Job
from vista.models.tenant import AgentRun, AgentRunEvent, Document, UsageEvent


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

        # Stub model call — deterministic fake usage numbers.
        input_tokens, output_tokens = 1200, 300
        model = "stub-model-v0"
        cost = Decimal(input_tokens) * Decimal("0.000003") + Decimal(output_tokens) * Decimal("0.000015")
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

        seq = _emit(session, run_id, seq, "result", {"summary": "stub analysis complete"})
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
