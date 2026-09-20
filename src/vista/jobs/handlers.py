import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select

from vista import documents
from vista.agents import analyze, discover, synthetic
from vista.agents.keys import agent_key_for
from vista.agents.llm import chat
from vista.agents.runtime import pricing as _pricing
from vista.agents.runtime import run_phase
from vista.config import settings
from vista.db import tenant_session
from vista.models.platform import Job
from vista.models.tenant import (
    AgentRun,
    AgentRunEvent,
    CompanySummary,
    Deal,
    Document,
    Employee,
    EmployeeAgent,
    Finding,
    Recording,
    RecordingReviewItem,
    UsageEvent,
)
from vista.review import SESSION_ID, status_for
from vista.review import explain as explain_section
from vista.storage import s3_client

FINDING_KINDS = {"observed_fact", "inefficiency", "proposed_automation"}


def _call_model(document: Document | None) -> tuple[str, str, int, int]:
    """Returns (model, output_text, input_tokens, output_tokens). Uses OpenAI when
    a key is configured, otherwise a deterministic stub."""
    if settings.openai_api_key:
        client = settings.openai_client()
        subject = f"a document named {document.filename!r}" if document else "a deal with no document"
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
            extra_body=settings.openai_extra_body(),
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
        resp = settings.openai_client().chat.completions.create(
            model=settings.openai_model,
            max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            extra_body=settings.openai_extra_body(),
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
        session.scalar(select(AgentRunEvent.seq).where(AgentRunEvent.run_id == run_id).order_by(AgentRunEvent.seq.desc()).limit(1)) or 0
    )


def _record_usage(session, run: AgentRun, model, input_tokens, output_tokens) -> None:
    in_price, out_price = _pricing(model)
    session.add(
        UsageEvent(
            run_id=run.id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=Decimal(input_tokens) * in_price + Decimal(output_tokens) * out_price,
            agent_key=run.agent_key,
            company=run.company,
        )
    )


def _start_run(session, run_id: uuid.UUID, tenant_schema: str) -> AgentRun:
    run = session.get(AgentRun, run_id)
    if run is None:
        raise RuntimeError(f"agent run {run_id} not found in {tenant_schema}")
    run.status = "running"
    run.started_at = run.started_at or datetime.now(UTC)
    run.agent_key = run.agent_key or agent_key_for(run.run_type)
    run.error = None
    return run


def _finding(run: AgentRun, **fields) -> Finding:
    return Finding(run_id=run.id, agent_key=run.agent_key, company=fields.pop("company", run.company), **fields)


def handle_employee_discovery(job: Job, tenant_schema: str) -> None:
    """Discovery run for one employee agent: given only the employee's role (and, in
    later phases, connector data), produce evidence-labeled findings.

    Phase 1: no connectors exist yet, so findings are role-based hypotheses and are
    labeled as such in `evidence` — the frontend should render them as unverified."""
    run_id = uuid.UUID(job.payload["run_id"])
    with tenant_session(tenant_schema) as session:
        run = _start_run(session, run_id, tenant_schema)
        agent = session.get(EmployeeAgent, run.employee_agent_id)
        employee = session.get(Employee, agent.employee_id)
        if agent.deal_id is not None and run.company is None:
            run.deal_id = agent.deal_id
            run.company = session.scalar(select(Deal.name).where(Deal.id == agent.deal_id))
        seq = _emit(
            session,
            run_id,
            _next_seq(session, run_id),
            "step",
            {"message": "discovery started", "employee_role": employee.role_title, "scopes": agent.scopes},
        )

        system = (
            "You are Vista, an operations-discovery agent embedded at a company acquired "
            "by a private-equity firm. You are assigned to one employee and must infer "
            "how their job likely works and where inefficiencies typically hide. "
            "You have NOT yet observed real data, so every finding is a hypothesis to "
            'verify. Respond with JSON only: {"findings": [{"kind": '
            '"observed_fact"|"inefficiency"|"proposed_automation", "title": str, '
            '"detail": str, "evidence": {"source": str, "confidence": '
            '"low"|"medium"|"high", "verify_by": str}}]}. 2-4 findings.'
        )
        user = f"Employee role: {employee.role_title}. Accessible scopes: {agent.scopes or ['none yet']}."
        model, text, itok, otok = _chat(system, user)
        seq = _emit(
            session,
            run_id,
            seq,
            "model_call",
            {"model": model, "input_tokens": itok, "output_tokens": otok},
        )
        _record_usage(session, run, model, itok, otok)

        findings_data = _parse_findings(text) if text else STUB_FINDINGS
        for f in findings_data:
            finding = _finding(
                run,
                employee_id=employee.id,
                agent_id=agent.id,
                kind=f["kind"],
                title=f["title"],
                detail=f["detail"],
                evidence=f["evidence"],
            )
            session.add(finding)
            session.flush()
            seq = _emit(
                session,
                run_id,
                seq,
                "finding",
                {"finding_id": str(finding.id), "kind": finding.kind, "title": finding.title},
            )

        _emit(session, run_id, seq, "result", {"findings_created": len(findings_data)})
        now = datetime.now(UTC)
        run.status = "succeeded"
        run.finished_at = now
        agent.last_run_at = now
        session.commit()


def handle_company_summary(job: Job, tenant_schema: str) -> None:
    """Aggregate all open findings across the company's employee agents into one
    operational summary."""
    run_id = uuid.UUID(job.payload["run_id"])
    with tenant_session(tenant_schema) as session:
        run = _start_run(session, run_id, tenant_schema)
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
                session,
                run_id,
                seq,
                "model_call",
                {"model": model, "input_tokens": itok, "output_tokens": otok},
            )
            _record_usage(session, run, model, itok, otok)
        else:
            text = "No open findings to summarize. Run employee-agent discovery first."

        summary = CompanySummary(run_id=run_id, content=text, stats=stats)
        session.add(summary)
        session.flush()
        _emit(session, run_id, seq, "result", {"summary_id": str(summary.id), **stats})
        run.status = "succeeded"
        run.finished_at = datetime.now(UTC)
        session.commit()


def _emit(session, run_id: uuid.UUID, seq: int, event_type: str, data: dict) -> int:
    session.add(AgentRunEvent(run_id=run_id, seq=seq, event_type=event_type, data=data))
    return seq + 1


def handle_agent_run(job: Job, tenant_schema: str) -> None:
    """Stub agent: 'reads' the target document and emits a summary event.
    Replace the model_call section with a real LLM call later."""
    run_id = uuid.UUID(job.payload["run_id"])
    with tenant_session(tenant_schema) as session:
        run = _start_run(session, run_id, tenant_schema)
        seq = _emit(session, run_id, _next_seq(session, run_id), "step", {"message": "run started"})

        document = session.get(Document, run.document_id) if run.document_id else None
        if document is not None:
            seq = _emit(
                session,
                run_id,
                seq,
                "tool_call",
                {"tool": "read_document", "s3_key": document.s3_key, "filename": document.filename},
            )

        model, output_text, input_tokens, output_tokens = _call_model(document)
        seq = _emit(
            session,
            run_id,
            seq,
            "model_call",
            {"model": model, "input_tokens": input_tokens, "output_tokens": output_tokens},
        )
        _record_usage(session, run, model, input_tokens, output_tokens)

        seq = _emit(session, run_id, seq, "result", {"summary": output_text})
        run.status = "succeeded"
        run.finished_at = datetime.now(UTC)
        session.commit()


def handle_explain_recording(job: Job, tenant_schema: str) -> None:
    """Explain every pending review item of one recording. Each item is committed
    on its own; a retry after a model outage redoes only the pending/failed ones.
    Every section is one model_call event + usage row on the recording_review run."""
    recording_id = uuid.UUID(job.payload["recording_id"])
    run_id = uuid.UUID(job.payload["run_id"]) if "run_id" in job.payload else None
    with tenant_session(tenant_schema) as session:
        pending = session.scalars(
            select(RecordingReviewItem.id).where(
                RecordingReviewItem.recording_id == recording_id, RecordingReviewItem.status.in_(("pending", "failed"))
            )
        ).all()
        if run_id is not None:
            run = _start_run(session, run_id, tenant_schema)
            seq = _emit(session, run_id, _next_seq(session, run_id), "step", {"message": "explaining sections", "sections": len(pending)})
            session.commit()
    failures = 0
    for item_id in pending:
        with tenant_session(tenant_schema) as session:
            item = session.get(RecordingReviewItem, item_id)
            if item is None or item.status not in ("pending", "failed"):
                continue
            run = session.get(AgentRun, run_id) if run_id is not None else None
            try:
                model, parsed, in_tokens, out_tokens = explain_section(item.prompt)
            except Exception as exc:  # noqa: BLE001 — one bad section must not block the rest
                failures += 1
                item.status, item.error = "failed", repr(exc)[:2000]
                if run is not None:
                    seq = _emit(session, run_id, seq, "error", {"section": item.item_id, "error": repr(exc)[:2000]})
            else:
                item.model, item.input_tokens, item.output_tokens = model, in_tokens, out_tokens
                item.label, item.explanation = parsed["label"], parsed["explanation"]
                item.confidence, item.unclear, item.questions = parsed["confidence"], parsed["unclear"], parsed["questions"]
                item.status, item.error = status_for(parsed["confidence"], item.threshold), None
                if run is not None:
                    seq = _emit(
                        session,
                        run_id,
                        seq,
                        "model_call",
                        {
                            "section": item.item_id,
                            "model": model,
                            "input_tokens": in_tokens,
                            "output_tokens": out_tokens,
                            "confidence": parsed["confidence"],
                            "status": item.status,
                        },
                    )
                    _record_usage(session, run, model, in_tokens, out_tokens)
            item.explained_at = item.updated_at = datetime.now(UTC)
            session.commit()
    if run_id is not None:
        with tenant_session(tenant_schema) as session:
            run = session.get(AgentRun, run_id)
            statuses = session.scalars(
                select(RecordingReviewItem.status).where(
                    RecordingReviewItem.recording_id == recording_id, RecordingReviewItem.item_id != SESSION_ID
                )
            ).all()
            _emit(
                session,
                run_id,
                seq,
                "result",
                {"sections": len(pending), "failed": failures, "open": sum(s in ("proposed", "unsure") for s in statuses)},
            )
            if not failures:
                run.status, run.finished_at, run.error = "succeeded", datetime.now(UTC), None
            session.commit()
    if failures:
        raise RuntimeError(f"{failures} of {len(pending)} sections could not be explained")


def handle_synthetic_discovery(job: Job, tenant_schema: str) -> None:
    """File Reviewer over one division of one synthetic company: each table is
    profiled in code, interpreted by the model, and every fact becomes an
    observed_fact finding with a file/column source_ref."""
    run_id = uuid.UUID(job.payload["run_id"])
    company = synthetic.company(job.payload["company"])
    division = job.payload["division"]
    with tenant_session(tenant_schema) as session:
        run = _start_run(session, run_id, tenant_schema)
        run.company, run.division, run.sector = company.short, division, company.sector
        seq = _emit(
            session,
            run_id,
            _next_seq(session, run_id),
            "step",
            {"message": "discovery started", "company": company.short, "division": division},
        )
        created = 0
        for table in synthetic.tables_for(company, division):
            profile = discover.profile_table(table)
            seq = _emit(
                session,
                run_id,
                seq,
                "tool_call",
                {"tool": "read_table", "ref": table.ref, "rows": profile.row_count, "flags": profile.flags},
            )
            phase = run_phase(discover.prepare(company, profile), discover.parse, lambda out, p=profile: discover.apply(out, p), llm=chat)
            r = phase.result
            seq = _emit(
                session,
                run_id,
                seq,
                "model_call",
                {"model": r.model, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "source": r.source},
            )
            _record_usage(session, run, r.model, r.input_tokens, r.output_tokens)
            for fact in phase.rows:
                finding = _finding(
                    run,
                    kind="observed_fact",
                    title=f"{fact['subject']}: {fact['predicate']}"[:512],
                    detail=fact["value"],
                    evidence={**fact["source_ref"], "confidence": fact["confidence"], "company": company.short},
                )
                session.add(finding)
                session.flush()
                seq = _emit(session, run_id, seq, "finding", {"finding_id": str(finding.id), "title": finding.title})
                created += 1
        _emit(session, run_id, seq, "result", {"findings_created": created})
        run.status = "succeeded"
        run.finished_at = datetime.now(UTC)
        session.commit()


def handle_synthetic_analyze(job: Job, tenant_schema: str) -> None:
    """Sector Merger / Portfolio Analyst over every synthetic company in one sector:
    one model call per opportunity kind, each seeing only that kind's table types;
    every cross-company opportunity becomes a proposed_automation finding whose
    evidence names the companies, shared key and table refs it rests on."""
    run_id = uuid.UUID(job.payload["run_id"])
    sector = job.payload["sector"]
    kinds = job.payload.get("kinds") or analyze.SECTOR_KINDS[sector]
    by_company = {c: synthetic.tables_for(c) for c in synthetic.companies() if c.sector == sector}
    shorts = {c.short for c in by_company}
    with tenant_session(tenant_schema) as session:
        run = _start_run(session, run_id, tenant_schema)
        run.sector = sector
        seq = _emit(
            session,
            run_id,
            _next_seq(session, run_id),
            "step",
            {"message": "analyze started", "sector": sector, "companies": sorted(shorts), "kinds": kinds},
        )
        created = 0
        for kind in kinds:
            refs = sorted(t.ref for tables in by_company.values() for t in analyze.tables_for_kind(kind, tables))
            seq = _emit(session, run_id, seq, "tool_call", {"tool": "read_tables", "kind": kind, "refs": refs})
            phase = run_phase(
                analyze.prepare(sector, by_company, kind),
                analyze.parse,
                lambda out, r=set(refs): analyze.apply(out, shorts, r),
                llm=chat,
            )
            r = phase.result
            seq = _emit(
                session,
                run_id,
                seq,
                "model_call",
                {"model": r.model, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "source": r.source},
            )
            _record_usage(session, run, r.model, r.input_tokens, r.output_tokens)
            for row in phase.rows:
                finding = _finding(
                    run,
                    company=", ".join(row["companies"])[:64],
                    kind="proposed_automation",
                    title=f"{row['kind']}: {row['title']}"[:512],
                    detail=row["detail"],
                    evidence={
                        "sector": sector,
                        "opportunity_kind": row["kind"],
                        "companies": row["companies"],
                        "shared_key": row["shared_key"],
                        "refs": row["evidence"],
                        "notes": row["notes"],
                        "estimated_annual_value": row["estimated_annual_value"],
                        "confidence": row["confidence"],
                    },
                )
                session.add(finding)
                session.flush()
                seq = _emit(session, run_id, seq, "finding", {"finding_id": str(finding.id), "title": finding.title})
                created += 1
            if phase.output.rejected:
                seq = _emit(
                    session, run_id, seq, "step", {"message": "look-alikes rejected", "kind": kind, "rejected": phase.output.rejected}
                )
        _emit(session, run_id, seq, "result", {"findings_created": created})
        run.status = "succeeded"
        run.finished_at = datetime.now(UTC)
        session.commit()


def handle_extract_recording_files(job: Job, tenant_schema: str) -> None:
    """Pull every queued document snapshot of a recording from object storage,
    extract bounded text/tables (vista.documents) and store the JSON next to the
    snapshot; the recording's file entry records the summary and the key."""
    recording_id = uuid.UUID(job.payload["recording_id"])
    with tenant_session(tenant_schema) as session:
        record = session.get(Recording, recording_id)
        if record is None:
            return
        queued = [f["id"] for f in record.files or [] if (f.get("extraction") or {}).get("status") in ("queued", "failed")]
        media = dict(record.media or {})
    failures = 0
    for file_id in queued:
        with tenant_session(tenant_schema) as session:
            record = session.get(Recording, recording_id)
            if record is None:
                return
            files = [dict(f) for f in record.files or []]
            entry = next((f for f in files if f.get("id") == file_id), None)
            if entry is None or (entry.get("extraction") or {}).get("status") not in ("queued", "failed"):
                continue
            item = media.get(entry.get("snapshot") or "")
            try:
                if item is None:
                    raise FileNotFoundError(entry.get("snapshot"))
                if item["size_bytes"] > documents.MAX_BYTES:
                    result = {"kind": "skipped", "error": "too large"}
                else:
                    obj = s3_client().get_object(Bucket=settings.s3_bucket, Key=item["key"])
                    with obj["Body"] as stream:
                        data = stream.read(documents.MAX_BYTES + 1)
                    result = documents.extract(data, entry.get("ext", ""))
                key = f"{item['key']}.extracted.json" if item else None
                if key:
                    body = json.dumps(result, ensure_ascii=False).encode()
                    s3_client().put_object(Bucket=settings.s3_bucket, Key=key, Body=body, ContentType="application/json")
                entry["extraction"] = {"status": "done", "key": key, "at": datetime.now(UTC).isoformat(), **documents.summary(result)}
            except (BotoCoreError, ClientError, FileNotFoundError) as exc:
                failures += 1
                entry["extraction"] = {"status": "failed", "error": repr(exc)[:500]}
            record.files = files
            record.updated_at = datetime.now(UTC)
            session.commit()
    if failures:
        raise RuntimeError(f"{failures} of {len(queued)} documents could not be read")


def mark_run_failed(job: Job, tenant_schema: str, error: str, permanent: bool) -> None:
    if "run_id" not in job.payload:
        return
    run_id = uuid.UUID(job.payload["run_id"])
    with tenant_session(tenant_schema) as session:
        run = session.get(AgentRun, run_id)
        if run is None:
            return
        seq = 1 + (
            session.scalar(select(AgentRunEvent.seq).where(AgentRunEvent.run_id == run_id).order_by(AgentRunEvent.seq.desc()).limit(1)) or 0
        )
        _emit(session, run_id, seq, "error", {"error": error[:2000], "permanent": permanent})
        run.error = error[:2000]
        if permanent:
            run.status = "failed"
            run.finished_at = datetime.now(UTC)
        else:
            run.status = "queued"
        session.commit()


HANDLERS = {
    "agent_run": handle_agent_run,
    "employee_discovery": handle_employee_discovery,
    "company_summary": handle_company_summary,
    "explain_recording": handle_explain_recording,
    "synthetic_discovery": handle_synthetic_discovery,
    "synthetic_analyze": handle_synthetic_analyze,
    "extract_recording_files": handle_extract_recording_files,
}
