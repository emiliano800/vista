"""The analyst's view of the agent ledger.

Every surface reads the same tenant tables (`agent_runs`, `agent_run_events`,
`findings`, `usage_events`, `company_summaries`); this module only derives the
shapes the analyst pages render from them. Nothing here writes: an "agent" is a
grouping of one tenant's runs by `agent_key`, a run's narrative is its event
stream, and a run's cost is the sum of its metered model calls.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from vista.agents.keys import AGENT_KEYS, AGENT_NAMES
from vista.models.tenant import AgentRun, AgentRunEvent, CompanySummary, Finding, UsageEvent
from vista.portfolio import serializers as ser

MAX_RUNS = 60  # newest runs per tenant that reach the snapshot
MAX_EVENTS = 80  # events per run kept in the snapshot (the trace API has the full stream)

REPRESENTS = {
    "file_reviewer": "Imported records — data quality and working-capital review",
    "sector_merger": "Cross-company comparison across the firm's sister companies",
    "report_generator": "Company narrative over open findings",
    "recording_reviewer": "Employee recordings — workflow evidence and questions",
}
RUN_STATUS = {"queued": "Queued", "running": "Running", "succeeded": "Complete", "failed": "Failed"}
FINDING_STATUS = {"open": "Open", "reviewed": "Reviewed", "dismissed": "Dismissed", "actioned": "Actioned"}
GOALS = {
    "canonical_review": "Review the canonical records the fact layer imported and surface evidence-linked observations.",
    "portfolio_merge": "Join canonical tables across the sector's companies on exact shared keys and validate each candidate.",
    "company_summary": "Summarize this company's open findings into one narrative, facts apart from hypotheses.",
    "deal_analysis": "Profile the uploaded documents and record observed facts with file and column evidence.",
    "synthetic_discovery": "Profile a division's exports and record observed facts with file and column evidence.",
    "employee_discovery": "Discover this role's workflows from the records it touches.",
    "synthetic_analyze": "Compare sister companies in a sector and propose shared-key opportunities.",
    "recording_review": "Explain the low-confidence stretches of an employee recording for their review.",
    "submission_analysis": "Analyze an employee's shared recording package into a private draft report.",
}


def agent_id(agent_key: str, scope_slug: str) -> str:
    return f"{agent_key}-{scope_slug}"


def finding_status_label(status: str | None) -> str:
    return FINDING_STATUS.get((status or "open").lower(), (status or "open").capitalize())


def _hhmm(at: datetime | None) -> str:
    return at.strftime("%H:%M") if at else "--:--"


def _event_line(e: AgentRunEvent) -> str:
    d = e.data or {}
    t = e.event_type
    if t == "step":
        return str(d.get("message") or "step")
    if t == "tool_call":
        tool = d.get("tool", "tool")
        if tool == "read_canonical" and isinstance(d.get("tables"), dict):
            return f"Read canonical rows: {', '.join(f'{k} ({v})' for k, v in d['tables'].items())}."
        extras = {k: v for k, v in d.items() if k != "tool" and not isinstance(v, (dict, list))}
        return f"{tool}" + (f" · {', '.join(f'{k}={v}' for k, v in extras.items())}" if extras else "")
    if t == "model_call":
        scope = d.get("table") or d.get("kind") or ""
        tokens = f"{d.get('input_tokens', 0)} in / {d.get('output_tokens', 0)} out tokens"
        return f"Model call{f' on {scope}' if scope else ''} ({d.get('model', 'model')}, {tokens})."
    if t == "finding":
        sev = f"{d['severity']}: " if d.get("severity") else ""
        return f"{sev}{d.get('title', 'finding')}"
    if t == "handoff":
        return f"Hand-off to {d.get('to', 'next agent')}" + (" (awaiting review)" if d.get("pending_review") else "")
    if t == "error":
        return f"Error: {d.get('message', 'unknown')}"
    if t == "result":
        parts = [f"{k.replace('_', ' ')} {v}" for k, v in d.items() if not isinstance(v, (dict, list))]
        return "Finished: " + (", ".join(parts) if parts else "done")
    return t


def _goal(run: AgentRun) -> str:
    goal = GOALS.get(run.run_type, f"{AGENT_NAMES.get(run.agent_key or '', run.run_type)} run.")
    scope = " · ".join(s for s in (run.division, run.sector.replace("_", " ") if run.sector else None) if s)
    return f"{goal} ({scope})" if scope else goal


def run_view(
    run: AgentRun,
    events: list[AgentRunEvent],
    cost: Decimal,
    summary: CompanySummary | None,
    findings: list[Finding],
    agent_ref: str,
    company_id: uuid.UUID | None,
) -> dict:
    sources: list[str] = []
    for e in events:
        d = e.data or {}
        if e.event_type == "tool_call" and d.get("tool") == "read_canonical" and isinstance(d.get("tables"), dict):
            sources += [f"canonical/{name}" for name in d["tables"]]
        elif e.event_type == "tool_call" and d.get("tool"):
            sources.append(str(d["tool"]))
    kept = events if len(events) <= MAX_EVENTS else events[: MAX_EVENTS - 1] + events[-1:]
    result = next((e for e in reversed(events) if e.event_type == "result"), None)
    output = summary.content if summary is not None else (_event_line(result) if result is not None else "")
    if run.status == "failed" and run.error:
        output = f"Failed: {run.error}"
    evidence: dict[str, int] = defaultdict(int)
    for f in findings:
        table = (f.evidence or {}).get("table") or (f.evidence or {}).get("file")
        if table:
            evidence[str(table)] += int((f.evidence or {}).get("record_count") or 1)
    return {
        "id": str(run.id),
        "agentId": agent_ref,
        "agentKey": run.agent_key,
        "runType": run.run_type,
        "companyId": ser.sid(company_id),
        "goal": _goal(run),
        "startedAt": ser.iso(run.started_at or run.created_at),
        "finishedAt": ser.iso(run.finished_at),
        "status": RUN_STATUS.get(run.status, run.status),
        "sources": list(dict.fromkeys(sources)),
        "events": [[_hhmm(e.created_at), _event_line(e)] for e in kept],
        "eventCount": len(events),
        "output": output,
        "evidence": [f"{table} · {n} record{'' if n == 1 else 's'}" for table, n in evidence.items()],
        "corrections": [],
        "modelCost": float(cost),
        "needsReview": sum(1 for f in findings if (f.severity or "") == "High" and f.status == "open"),
        "error": run.error,
    }


def finding_view(f: Finding, agent_ref: str, company_id: uuid.UUID | None) -> dict:
    return {
        "id": f.ref or str(f.id),
        "uuid": str(f.id),
        "ref": f.ref,
        "companyId": ser.sid(f.company_id or company_id),
        "title": f.title,
        "detail": f.detail,
        "kind": f.kind,
        "severity": f.severity or "Low",
        "status": finding_status_label(f.status),
        "agentId": agent_ref,
        "agentKey": f.agent_key,
        "runId": str(f.run_id),
        "foundAt": ser.iso(f.created_at),
        "findingType": f.finding_type,
        "effect": f.effect,
        "evidence": f.evidence or {},
        "syntheticDemo": f.synthetic_demo,
    }


def agent_view(agent_key: str, scope_slug: str, company_id: uuid.UUID | None, runs: list[dict], findings: list[dict]) -> dict:
    ordered = sorted(runs, key=lambda r: r["startedAt"] or "")
    last = ordered[-1] if ordered else None
    last_failed = next((r for r in reversed(ordered) if r["status"] == "Failed"), None)
    if any(r["status"] in ("Queued", "Running") for r in ordered):
        status = "Running"
    elif last is not None and last["status"] == "Failed":
        status = "Failed"
    else:
        status = "Active"
    return {
        "id": agent_id(agent_key, scope_slug),
        "agentKey": agent_key,
        "companyId": ser.sid(company_id),
        "name": AGENT_NAMES.get(agent_key, agent_key),
        "represents": REPRESENTS.get(agent_key, ""),
        "status": status,
        "lastRunAt": last["startedAt"] if last else None,
        "cases": len(ordered),
        "review": sum(1 for f in findings if f["status"] == "Open" and f["severity"] == "High"),
        "findings": len(findings),
        "lastFailure": last_failed["error"] if last_failed else None,
        "cost": round(sum(r["modelCost"] for r in ordered), 4),
    }


def tenant_ledger(ts: Session, scope_slug: str, company_id: uuid.UUID | None) -> tuple[list[dict], list[dict], list[dict]]:
    """(agents, runs, findings) for one tenant schema, newest runs first. `company_id` is the
    firm company the schema belongs to, or None for the firm's home tenant (sector merges)."""
    runs = ts.scalars(select(AgentRun).where(AgentRun.agent_key.in_(AGENT_KEYS)).order_by(AgentRun.created_at.desc()).limit(MAX_RUNS)).all()
    run_ids = [r.id for r in runs]
    events: dict[uuid.UUID, list[AgentRunEvent]] = defaultdict(list)
    cost: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
    summaries: dict[uuid.UUID, CompanySummary] = {}
    if run_ids:
        stream = select(AgentRunEvent).where(AgentRunEvent.run_id.in_(run_ids)).order_by(AgentRunEvent.run_id, AgentRunEvent.seq)
        for e in ts.scalars(stream):
            events[e.run_id].append(e)
        for run_id, amount in ts.execute(select(UsageEvent.run_id, UsageEvent.cost_usd).where(UsageEvent.run_id.in_(run_ids))):
            cost[run_id] += Decimal(amount or 0)
        for s in ts.scalars(select(CompanySummary).where(CompanySummary.run_id.in_(run_ids)).order_by(CompanySummary.created_at)):
            summaries[s.run_id] = s
    findings = ts.scalars(select(Finding).order_by(Finding.created_at)).all()
    by_run: dict[uuid.UUID, list[Finding]] = defaultdict(list)
    for f in findings:
        by_run[f.run_id].append(f)
    run_views = [
        run_view(r, events[r.id], cost[r.id], summaries.get(r.id), by_run[r.id], agent_id(r.agent_key, scope_slug), company_id)
        for r in runs
    ]
    finding_views = [finding_view(f, agent_id(f.agent_key or "file_reviewer", scope_slug), company_id) for f in findings]
    by_key_runs: dict[str, list[dict]] = defaultdict(list)
    for r in run_views:
        by_key_runs[r["agentKey"]].append(r)
    by_key_findings: dict[str, list[dict]] = defaultdict(list)
    for f in finding_views:
        by_key_findings[f["agentKey"] or "file_reviewer"].append(f)
    agents = [
        agent_view(key, scope_slug, company_id, by_key_runs.get(key, []), by_key_findings.get(key, []))
        for key in AGENT_KEYS
        if key in by_key_runs or key in by_key_findings
    ]
    return agents, run_views, finding_views
