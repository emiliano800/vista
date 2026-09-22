"""Cloud analysis of one accepted recorder submission (upload protocol 2).

The activity artifact is metadata only — timestamps, app names, interaction types
and counts. Window titles, URLs, typed text, clipboard contents and screenshots
never leave the employee's computer, so nothing here can name a document, a
customer or a system beyond the application. Everything an analyst later reads is
therefore split in three, and the split is preserved in the stored report:

  observed        deterministic facts computed in code from the events
                  (time per app, switches, copy→paste transfers, ping-pong loops,
                  work stretches). Never touched by a model.
  interpretation  the Recording Reviewer's reading of those facts plus the
                  documents the employee chose to share. Every item must point at
                  apps that actually appear in `observed`; anything else is dropped.
  questions       focused prompts the employee answers before publishing.

The handler is idempotent per run: a retry recomputes the same draft and never
publishes anything. Publication is a separate, human action on the API.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from vista import documents
from vista.agents.llm import Prompt, chat, strip_fences
from vista.agents.runtime import run_phase
from vista.db import tenant_session
from vista.models.platform import Job
from vista.models.tenant import AgentRun, AgentRunEvent, RecorderReport, RecorderSubmission

IDLE_GAP_S = 120  # a focus span ends when nothing happens for this long
STRETCH_GAP_S = 300  # work stretches are separated by gaps of at least this long
TRANSFER_WINDOW_S = 120  # copy in app A → paste in app B within this window counts as a transfer
MAX_QUESTIONS = 6
MAX_DOCUMENT_EXCERPT = 1200
COVERAGE_EXCLUDED = ["window_title", "url", "typed_text", "clipboard", "screenshots", "video", "raw_events"]


# ---- observed facts (pure code) -------------------------------------------------


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _clock(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%H:%M")


def observe(events: list[dict], manifest: dict) -> dict:
    """Deterministic facts from metadata-only events. Input rows are
    {timestamp, event_type, app, count}; output is JSON-serialisable."""
    rows = sorted(
        ({"t": _ts(e["timestamp"]), "type": e["event_type"], "app": str(e["app"])[:128], "n": int(e.get("count") or 1)} for e in events),
        key=lambda r: r["t"],
    )
    per_app: dict[str, dict] = defaultdict(
        lambda: {"active_s": 0.0, "events": 0, "clicks": 0, "keys": 0, "copies": 0, "pastes": 0, "spans": 0}
    )
    transitions: Counter = Counter()
    transition_gap: dict[tuple[str, str], list[float]] = defaultdict(list)
    transfers: Counter = Counter()
    transfer_latency: dict[tuple[str, str], list[float]] = defaultdict(list)
    internal_pastes = 0
    stretches: list[dict] = []
    switches = 0
    last_copy: dict | None = None
    previous: dict | None = None
    span_app: str | None = None
    span_start: datetime | None = None
    stretch: dict | None = None

    def close_span(end: datetime) -> None:
        nonlocal span_app, span_start
        if span_app is not None and span_start is not None:
            per_app[span_app]["active_s"] += max(0.0, (end - span_start).total_seconds())
            per_app[span_app]["spans"] += 1
        span_app, span_start = None, None

    for row in rows:
        app, t = row["app"], row["t"]
        stats = per_app[app]
        stats["events"] += row["n"]
        if row["type"] == "click":
            stats["clicks"] += row["n"]
        elif row["type"] == "key":
            stats["keys"] += row["n"]
        elif row["type"] == "copy":
            stats["copies"] += row["n"]
            last_copy = row
        elif row["type"] == "paste":
            stats["pastes"] += row["n"]
            if last_copy is not None and 0 <= (t - last_copy["t"]).total_seconds() <= TRANSFER_WINDOW_S:
                if last_copy["app"] != app:
                    transfers[(last_copy["app"], app)] += 1
                    transfer_latency[(last_copy["app"], app)].append((t - last_copy["t"]).total_seconds())
                else:
                    internal_pastes += 1
        gap = (t - previous["t"]).total_seconds() if previous else 0.0
        idle = previous is not None and gap > IDLE_GAP_S
        if idle:
            close_span(previous["t"])  # nothing happened: the span ends at the last event
        if stretch is None or gap > STRETCH_GAP_S:
            if stretch is not None:
                stretches.append(stretch)
            stretch = {"start": t, "end": t, "events": 0, "switches": 0, "apps": Counter()}
        stretch["end"] = t
        stretch["events"] += row["n"]
        stretch["apps"][app] += row["n"]
        if previous is not None and previous["app"] != app:
            switches += 1
            stretch["switches"] += 1
            transitions[(previous["app"], app)] += 1
            transition_gap[(previous["app"], app)].append(gap)
            if not idle:
                close_span(t)
        if span_app is None:
            span_app, span_start = app, t
        previous = row
    if previous is not None:
        close_span(previous["t"])
    if stretch is not None:
        stretches.append(stretch)

    total_active = sum(v["active_s"] for v in per_app.values())
    total_events = sum(v["events"] for v in per_app.values()) or 1
    apps = [
        {
            "app": app,
            # Share of active time; when no span has any length (one-event sessions) fall back to events.
            "share": round(v["active_s"] / total_active, 3) if total_active else round(v["events"] / total_events, 3),
            **{k: (round(x, 1) if isinstance(x, float) else x) for k, x in v.items()},
        }
        for app, v in sorted(per_app.items(), key=lambda kv: (-kv[1]["active_s"], -kv[1]["events"], kv[0]))
    ]
    loops = Counter()
    for (a, b), n in transitions.items():
        back = transitions.get((b, a), 0)
        if back and a < b:
            loops[(a, b)] = n + back
    return {
        "events": sum(r["n"] for r in rows),
        "apps": apps,
        "switches": switches,
        "transitions": [
            {"from": a, "to": b, "count": n, "mean_gap_s": round(sum(transition_gap[(a, b)]) / len(transition_gap[(a, b)]), 1)}
            for (a, b), n in transitions.most_common(20)
        ],
        "transfers": [
            {"from": a, "to": b, "count": n, "mean_latency_s": round(sum(transfer_latency[(a, b)]) / len(transfer_latency[(a, b)]), 1)}
            for (a, b), n in transfers.most_common(20)
        ],
        "internal_pastes": internal_pastes,
        "loops": [{"between": [a, b], "count": n} for (a, b), n in loops.most_common(10)],
        "stretches": [
            {
                "id": f"s{i + 1}",
                "start": s["start"].isoformat(),
                "end": s["end"].isoformat(),
                "duration_s": round((s["end"] - s["start"]).total_seconds(), 1),
                "events": s["events"],
                "switches": s["switches"],
                "apps": [a for a, _ in s["apps"].most_common(6)],
            }
            for i, s in enumerate(stretches)
        ],
        "session": {
            "started_at": manifest["started_at"],
            "ended_at": manifest["ended_at"],
            "active_seconds": manifest.get("active_seconds"),
        },
    }


def coverage_for(observed: dict, document_count: int) -> dict:
    return {
        "sharing_policy": "activity-metadata-v1",
        "fields": ["timestamp", "event_type", "app", "count"],
        "excluded": COVERAGE_EXCLUDED,
        "events": observed["events"],
        "apps": len(observed["apps"]),
        "documents": document_count,
        "note": (
            "Activities are known only at application level; documents, records and web pages the employee "
            "worked on are not observable unless a document snapshot was shared."
        ),
    }


def deterministic_questions(observed: dict) -> list[dict]:
    """Questions only the employee can answer, each anchored to observed facts."""
    out: list[dict] = []
    busy = sorted((s for s in observed["stretches"] if s["switches"] >= 6 and len(s["apps"]) >= 2), key=lambda s: -s["switches"])
    for s in busy[:2]:
        a, b = s["apps"][0], s["apps"][1]
        out.append(
            {
                "id": f"q{len(out) + 1}",
                "source": "observed",
                "question": (
                    f"Between {_clock(_ts(s['start']))} and {_clock(_ts(s['end']))} you moved between {a} and {b} "
                    f"{s['switches']} times. What were you working on, and what did you take from one to the other?"
                ),
                "about": {"stretch": s["id"], "apps": [a, b], "start": s["start"], "end": s["end"]},
            }
        )
    for t in observed["transfers"][:2]:
        out.append(
            {
                "id": f"q{len(out) + 1}",
                "source": "observed",
                "question": (
                    f"You copied from {t['from']} and pasted into {t['to']} {t['count']} time{'s' if t['count'] != 1 else ''}. "
                    "What information moves between them, and does it get re-keyed anywhere else?"
                ),
                "about": {"transfer": [t["from"], t["to"]], "apps": [t["from"], t["to"]]},
            }
        )
    if observed["apps"] and observed["apps"][0]["share"] >= 0.4 and len(out) < MAX_QUESTIONS:
        top = observed["apps"][0]
        out.append(
            {
                "id": f"q{len(out) + 1}",
                "source": "observed",
                "question": (
                    f"Most of the session ({round(top['share'] * 100)}%) was in {top['app']}. "
                    "Which task were you doing there, and is it something you repeat every day or week?"
                ),
                "about": {"apps": [top["app"]]},
            }
        )
    return out[:MAX_QUESTIONS]


# ---- document context (shared snapshots only) ---------------------------------


def document_context(filename: str, data: bytes) -> dict:
    """A bounded description of one shared document for the prompt and the report."""
    ext = PurePosixPath(filename).suffix.lower()
    result = documents.extract(data, ext) if documents.can_extract(ext) else {"kind": "unsupported"}
    summary = documents.summary(result)
    excerpt = ""
    kind = result.get("kind")
    if kind == "table":
        excerpt = "\n".join(",".join(row) for row in result["rows"][:6])
    elif kind == "workbook":
        first = result["sheets"][0] if result["sheets"] else None
        excerpt = f"Sheets: {', '.join(s['name'] for s in result['sheets'][:MAX_SHEET_NAMES])}"
        if first and first.get("rows"):
            excerpt += "\n" + "\n".join(",".join(row) for row in first["rows"][:6])
    elif kind == "document":
        excerpt = "\n".join(result["paragraphs"][:8])
    elif kind == "presentation":
        excerpt = "\n".join(" ".join(slide.get("text", [])) for slide in result["slides"][:5])
    elif kind in ("pdf", "text"):
        excerpt = result.get("text", "")
    return {"filename": filename, "summary": summary, "excerpt": excerpt[:MAX_DOCUMENT_EXCERPT]}


MAX_SHEET_NAMES = 10


# ---- model interpretation (prepare → chat → parse → apply) ---------------------

SYSTEM = (
    "You are Vista's Recording Reviewer. You read a metadata-only summary of one employee's work session "
    "(application names, timing, switches, copy/paste transfers) plus excerpts of documents the employee chose "
    "to share. You never see window titles, URLs, typed text or screenshots, so say only what the applications "
    "and timing support. Return strict JSON: "
    '{"summary": string, '
    '"workflows": [{"name": string, "apps": [string], "evidence": string, "confidence": number}], '
    '"automation_candidates": [{"title": string, "rationale": string, "apps": [string], "evidence": string, '
    '"confidence": number}], '
    '"questions": [{"question": string, "apps": [string]}]}. '
    "Use only application names that appear in the observed facts. Confidence is 0..1. Ask at most three "
    "questions, each one only the employee could answer. Describe hypotheses as hypotheses; do not present them "
    "as observed facts."
)


class ModelWorkflow(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=200)
    apps: list[str] = Field(default_factory=list, max_length=10)
    evidence: str = Field(default="", max_length=1000)
    confidence: float = Field(default=0.5, ge=0, le=1)


class ModelCandidate(ModelWorkflow):
    title: str = Field(min_length=1, max_length=200)
    name: str = ""
    rationale: str = Field(default="", max_length=1000)


class ModelQuestion(BaseModel):
    model_config = ConfigDict(extra="ignore")
    question: str = Field(min_length=1, max_length=500)
    apps: list[str] = Field(default_factory=list, max_length=10)


class ModelOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    summary: str = Field(default="", max_length=4000)
    workflows: list[ModelWorkflow] = Field(default_factory=list, max_length=12)
    automation_candidates: list[ModelCandidate] = Field(default_factory=list, max_length=12)
    questions: list[ModelQuestion] = Field(default_factory=list, max_length=6)


def prepare(observed: dict, docs: list[dict]) -> Prompt:
    facts = {k: observed[k] for k in ("events", "apps", "switches", "transitions", "transfers", "loops", "stretches", "session")}
    user = json.dumps({"observed": facts, "shared_documents": docs}, default=str)
    return Prompt(system=SYSTEM, user=user, max_tokens=1600)


def parse(text: str) -> ModelOutput | None:
    if not text.strip():
        return None
    try:
        return ModelOutput.model_validate(json.loads(strip_fences(text)))
    except (json.JSONDecodeError, ValidationError, TypeError):
        return None


def apply_interpretation(output: ModelOutput | None, observed: dict, *, model: str, source: str) -> tuple[dict, list[dict]]:
    """Keep only items whose apps exist in the observed facts; return
    (interpretation, extra questions)."""
    known = {a["app"] for a in observed["apps"]}
    known_ci = {a.casefold(): a for a in known}

    def canon(apps: list[str]) -> list[str]:
        return [known_ci[a.casefold()] for a in apps if a.casefold() in known_ci]

    if output is None:
        return {"source": source, "model": model, "summary": "", "workflows": [], "automation_candidates": [], "rejected": 0}, []
    rejected = 0
    workflows, candidates, questions = [], [], []
    for w in output.workflows:
        apps = canon(w.apps)
        if not apps:
            rejected += 1
            continue
        workflows.append({"name": w.name, "apps": apps, "evidence": w.evidence, "confidence": round(w.confidence, 2)})
    for c in output.automation_candidates:
        apps = canon(c.apps)
        if not apps:
            rejected += 1
            continue
        candidates.append(
            {"title": c.title, "rationale": c.rationale, "apps": apps, "evidence": c.evidence, "confidence": round(c.confidence, 2)}
        )
    for q in output.questions[:3]:
        apps = canon(q.apps)
        if q.apps and not apps:
            rejected += 1
            continue
        questions.append({"source": "model", "question": q.question, "about": {"apps": apps}})
    interpretation = {
        "source": source,
        "model": model,
        "summary": output.summary,
        "workflows": workflows,
        "automation_candidates": candidates,
        "rejected": rejected,
    }
    return interpretation, questions


def merge_questions(deterministic: list[dict], model: list[dict]) -> list[dict]:
    out = [dict(q, answer=None, answered_at=None) for q in deterministic]
    seen = {re.sub(r"\W+", " ", q["question"]).casefold() for q in out}
    for q in model:
        key = re.sub(r"\W+", " ", q["question"]).casefold()
        if key in seen or len(out) >= MAX_QUESTIONS:
            continue
        seen.add(key)
        out.append({"id": f"q{len(out) + 1}", **q, "answer": None, "answered_at": None})
    return out


# ---- the job -------------------------------------------------------------------


def _emit(session, run_id: uuid.UUID, seq: int, event_type: str, data: dict) -> int:
    session.add(AgentRunEvent(run_id=run_id, seq=seq, event_type=event_type, data=data))
    return seq + 1


def handle_analyze_submission(job: Job, tenant_schema: str) -> None:
    """One accepted submission → one draft RecorderReport. Reads the verified
    artifacts back from object storage (never trusting the manifest alone),
    computes observed facts in code, asks the model for an interpretation, and
    stores the draft. Retries recompute; nothing is published here."""
    from vista.jobs.handlers import _next_seq, _record_usage, _start_run  # noqa: PLC0415 — avoid an import cycle
    from vista.recorder_uploads import read_artifact  # noqa: PLC0415

    submission_id = uuid.UUID(job.payload["submission_id"])
    run_id = uuid.UUID(job.payload["run_id"])
    with tenant_session(tenant_schema) as session:
        row = session.get(RecorderSubmission, submission_id)
        if row is None:
            raise RuntimeError(f"submission {submission_id} not found")
        if row.upload_status != "accepted":
            raise RuntimeError("submission has not been accepted")
        _start_run(session, run_id, tenant_schema)
        row.analysis_status, row.analysis_error = "running", None
        seq = _next_seq(session, run_id)
        seq = _emit(session, run_id, seq, "step", {"message": "started", "submission_id": str(submission_id), "parent_run_id": None})
        session.commit()
    try:
        _analyze(tenant_schema, submission_id, run_id, seq, read_artifact, _record_usage)
    except Exception as exc:
        with tenant_session(tenant_schema) as session:
            row = session.get(RecorderSubmission, submission_id)
            if row is not None:
                row.analysis_status, row.analysis_error = "failed", repr(exc)[:2000]
                session.commit()
        raise


def _analyze(tenant_schema: str, submission_id: uuid.UUID, run_id: uuid.UUID, seq: int, read_artifact, record_usage) -> None:
    with tenant_session(tenant_schema) as session:
        row = session.get(RecorderSubmission, submission_id)
        run = session.get(AgentRun, run_id)
        verified = {v["id"]: v for v in row.verified_artifacts}
        activity = None
        docs: list[dict] = []
        for artifact in row.manifest["artifacts"]:
            data = read_artifact(tenant_schema, row, artifact, expected=verified.get(artifact["id"]))
            seq = _emit(session, run_id, seq, "tool_call", {"tool": "read_artifact", "artifact": artifact["id"], "bytes": len(data)})
            if artifact["kind"] == "activity":
                activity = json.loads(data)
            else:
                docs.append(document_context(artifact["filename"], data))
        if activity is None:
            raise RuntimeError("activity artifact missing")
        observed = observe(activity["events"], row.manifest)
        seq = _emit(
            session,
            run_id,
            seq,
            "tool_call",
            {
                "tool": "observe_activity",
                "events": observed["events"],
                "apps": len(observed["apps"]),
                "switches": observed["switches"],
                "stretches": len(observed["stretches"]),
            },
        )
        phase = run_phase(prepare(observed, docs), parse, lambda output: [], llm=chat)
        interpretation, model_questions = apply_interpretation(phase.output, observed, model=phase.result.model, source=phase.result.source)
        seq = _emit(
            session,
            run_id,
            seq,
            "model_call",
            {
                "model": phase.result.model,
                "source": phase.result.source,
                "input_tokens": phase.result.input_tokens,
                "output_tokens": phase.result.output_tokens,
                "parsed": phase.output is not None,
                "rejected": interpretation["rejected"],
            },
        )
        record_usage(session, run, phase.result.model, phase.result.input_tokens, phase.result.output_tokens)
        questions = merge_questions(deterministic_questions(observed), model_questions)
        report = session.scalar(select(RecorderReport).where(RecorderReport.submission_id == submission_id).with_for_update())
        now = datetime.now(UTC)
        if report is None:
            report = RecorderReport(
                submission_id=submission_id,
                uploaded_by=row.uploaded_by,
                canonical_company_id=row.canonical_company_id,
                workspace=row.manifest["workspace"],
            )
            session.add(report)
        elif report.status == "published":
            raise RuntimeError("a published report cannot be replaced by re-analysis")
        else:
            # Keep answers the employee already gave to identical questions.
            previous = {q["question"]: q for q in report.questions}
            for q in questions:
                if q["question"] in previous and previous[q["question"]].get("answer"):
                    q["answer"], q["answered_at"] = previous[q["question"]]["answer"], previous[q["question"]]["answered_at"]
        report.run_id = run_id
        report.coverage = coverage_for(observed, len(docs))
        report.observed = observed
        report.interpretation = {**interpretation, "documents": [{"filename": d["filename"], "summary": d["summary"]} for d in docs]}
        report.questions = questions
        report.updated_at = now
        row.analysis_status, row.analysis_error, row.analysis_run_id = "succeeded", None, run_id
        session.flush()
        _emit(
            session,
            run_id,
            seq,
            "result",
            {
                "report_id": str(report.id),
                "workflows": len(interpretation["workflows"]),
                "candidates": len(interpretation["automation_candidates"]),
                "questions": len(questions),
            },
        )
        run.status, run.finished_at, run.error = "succeeded", now, None
        session.commit()


def public_report(report: RecorderReport, *, full: bool) -> dict[str, Any]:
    open_questions = sum(1 for q in report.questions if not q.get("answer"))
    out: dict[str, Any] = {
        "id": str(report.id),
        "submission_id": str(report.submission_id),
        "run_id": str(report.run_id) if report.run_id else None,
        "status": report.status,
        "workspace": report.workspace,
        "canonical_company_id": str(report.canonical_company_id) if report.canonical_company_id else None,
        "session": report.observed.get("session"),
        "coverage": report.coverage,
        "summary": report.interpretation.get("summary", ""),
        "apps": [a["app"] for a in report.observed.get("apps", [])[:6]],
        "switches": report.observed.get("switches", 0),
        "workflows": len(report.interpretation.get("workflows", [])),
        "automation_candidates": len(report.interpretation.get("automation_candidates", [])),
        "questions_open": open_questions,
        "questions_total": len(report.questions),
        "updated_at": report.updated_at,
        "published_at": report.published_at,
    }
    if full:
        out["observed"] = report.observed
        out["interpretation"] = report.interpretation
        out["questions"] = report.questions
    return out
