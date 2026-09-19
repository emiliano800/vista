import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from vista.config import settings
from vista.db import tenant_session
from vista.models.platform import Job
from vista.models.tenant import (
    AgentRun,
    AgentRunEvent,
    CompanySummary,
    Document,
    Employee,
    EmployeeAgent,
    Finding,
    UsageEvent,
)

FINDING_KINDS = {"observed_fact", "inefficiency", "proposed_automation"}

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


def _chat(system: str, user: str, max_tokens: int = 800) -> tuple[str, str, int, int]:
    """One model call. Returns (model, text, input_tokens, output_tokens)."""
    if settings.openai_api_key:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        resp = client.chat.completions.create(
            model=settings.openai_model,
            max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return (
            resp.model,
            resp.choices[0].message.content or "",
            resp.usage.prompt_tokens,
            resp.usage.completion_tokens,
        )
    return "stub-model-v0", "", 800, 200


def _parse_findings(text: str) -> list[dict]:
    """Parse the model's JSON findings; tolerate code fences; fall back to one
    observed_fact wrapping the raw text."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        cleaned = cleaned.removeprefix("json").strip()
    try:
        data = json.loads(cleaned)
        findings = data["findings"] if isinstance(data, dict) else data
        out = []
        for f in findings:
            kind = f.get("kind") if f.get("kind") in FINDING_KINDS else "observed_fact"
            out.append(
                {
                    "kind": kind,
                    "title": str(f.get("title", "untitled"))[:512],
                    "detail": str(f.get("detail", "")),
                    "evidence": f.get("evidence") or {},
                }
            )
        return out
    except (json.JSONDecodeError, TypeError, KeyError, AttributeError):
        return [
            {
                "kind": "observed_fact",
                "title": "Unstructured agent output",
                "detail": text,
                "evidence": {"note": "model output was not valid JSON"},
            }
        ]


STUB_FINDINGS = [
    {
        "kind": "inefficiency",
        "title": "Manual invoice data entry",
        "detail": "Invoices likely arrive as email PDFs and are retyped into the accounting system.",
        "evidence": {"source": "role-hypothesis", "confidence": "low"},
    },
    {
        "kind": "proposed_automation",
        "title": "Automate invoice ingestion",
        "detail": "Import invoice PDFs directly and pre-fill accounting entries for review.",
        "evidence": {"source": "role-hypothesis", "confidence": "low"},
    },
]


def _next_seq(session, run_id: uuid.UUID) -> int:
    """First unused event seq for a run (safe across retries)."""
    return 1 + (
        session.scalar(
            select(AgentRunEvent.seq)
            .where(AgentRunEvent.run_id == run_id)
            .order_by(AgentRunEvent.seq.desc())
            .limit(1)
        )
        or 0
    )


def _record_usage(session, run_id, model, input_tokens, output_tokens) -> None:
    in_price, out_price = _pricing(model)
    session.add(
        UsageEvent(
            run_id=run_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=Decimal(input_tokens) * in_price + Decimal(output_tokens) * out_price,
        )
    )


def handle_employee_discovery(job: Job, tenant_schema: str) -> None:
    """Discovery run for one employee agent: given only the employee's role (and, in
    later phases, connector data), produce evidence-labeled findings.

    Phase 1: no connectors exist yet, so findings are role-based hypotheses and are
    labeled as such in `evidence` — the frontend should render them as unverified."""
    run_id = uuid.UUID(job.payload["run_id"])
    with tenant_session(tenant_schema) as session:
        run = session.get(AgentRun, run_id)
        if run is None:
            raise RuntimeError(f"agent run {run_id} not found in {tenant_schema}")
        run.status = "running"
        agent = session.get(EmployeeAgent, run.employee_agent_id)
        employee = session.get(Employee, agent.employee_id)
        seq = _emit(
            session, run_id, _next_seq(session, run_id), "step",
            {"message": "discovery started", "employee_role": employee.role_title,
             "scopes": agent.scopes},
        )

        system = (
            "You are Vista, an operations-discovery agent embedded at a company acquired "
            "by a private-equity firm. You are assigned to one employee and must infer "
            "how their job likely works and where inefficiencies typically hide. "
            "You have NOT yet observed real data, so every finding is a hypothesis to "
            "verify. Respond with JSON only: {\"findings\": [{\"kind\": "
            "\"observed_fact\"|\"inefficiency\"|\"proposed_automation\", \"title\": str, "
            "\"detail\": str, \"evidence\": {\"source\": str, \"confidence\": "
            "\"low\"|\"medium\"|\"high\", \"verify_by\": str}}]}. 2-4 findings."
        )
        user = f"Employee role: {employee.role_title}. Accessible scopes: {agent.scopes or ['none yet']}."
        model, text, itok, otok = _chat(system, user)
        seq = _emit(
            session, run_id, seq, "model_call",
            {"model": model, "input_tokens": itok, "output_tokens": otok},
        )
        _record_usage(session, run_id, model, itok, otok)

        findings_data = _parse_findings(text) if text else STUB_FINDINGS
        for f in findings_data:
            finding = Finding(
                run_id=run_id, employee_id=employee.id, agent_id=agent.id,
                kind=f["kind"], title=f["title"], detail=f["detail"], evidence=f["evidence"],
            )
            session.add(finding)
            session.flush()
            seq = _emit(
                session, run_id, seq, "finding",
                {"finding_id": str(finding.id), "kind": finding.kind, "title": finding.title},
            )

        _emit(session, run_id, seq, "result", {"findings_created": len(findings_data)})
        now = datetime.now(timezone.utc)
        run.status = "succeeded"
        run.finished_at = now
        agent.last_run_at = now
        session.commit()


def handle_company_summary(job: Job, tenant_schema: str) -> None:
    """Aggregate all open findings across the company's employee agents into one
    operational summary."""
    run_id = uuid.UUID(job.payload["run_id"])
    with tenant_session(tenant_schema) as session:
        run = session.get(AgentRun, run_id)
        if run is None:
            raise RuntimeError(f"agent run {run_id} not found in {tenant_schema}")
        run.status = "running"
        seq = _emit(session, run_id, _next_seq(session, run_id), "step", {"message": "company summary started"})

        rows = session.execute(
            select(Finding, Employee.role_title)
            .outerjoin(Employee, Employee.id == Finding.employee_id)
            .where(Finding.status == "open")
            .order_by(Finding.created_at)
        ).all()
        by_kind: dict[str, int] = {}
        lines = []
        employee_ids = set()
        for finding, role in rows:
            by_kind[finding.kind] = by_kind.get(finding.kind, 0) + 1
            if finding.employee_id:
                employee_ids.add(finding.employee_id)
            lines.append(f"- [{finding.kind}] ({role or 'company'}) {finding.title}: {finding.detail}")
        stats = {
            "open_findings": len(rows),
            "by_kind": by_kind,
            "employees_covered": len(employee_ids),
        }
        seq = _emit(session, run_id, seq, "tool_call", {"tool": "read_findings", **stats})

        if rows:
            system = (
                "You are Vista, summarizing operational findings across one acquired "
                "company for its private-equity owner. Group related findings, highlight "
                "the highest-impact inefficiencies, and clearly separate verified facts "
                "from unverified hypotheses. Be concise: a few short paragraphs or bullets."
            )
            model, text, itok, otok = _chat(system, "\n".join(lines), max_tokens=1000)
            if not text:
                text = (
                    f"{len(rows)} open findings across {len(employee_ids)} employees: "
                    + ", ".join(f"{v} {k}" for k, v in sorted(by_kind.items()))
                    + ". (stub summary)"
                )
            seq = _emit(
                session, run_id, seq, "model_call",
                {"model": model, "input_tokens": itok, "output_tokens": otok},
            )
            _record_usage(session, run_id, model, itok, otok)
        else:
            text = "No open findings to summarize. Run employee-agent discovery first."

        summary = CompanySummary(run_id=run_id, content=text, stats=stats)
        session.add(summary)
        session.flush()
        _emit(session, run_id, seq, "result", {"summary_id": str(summary.id), **stats})
        run.status = "succeeded"
        run.finished_at = datetime.now(timezone.utc)
        session.commit()


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


HANDLERS = {
    "agent_run": handle_agent_run,
    "employee_discovery": handle_employee_discovery,
    "company_summary": handle_company_summary,
}
