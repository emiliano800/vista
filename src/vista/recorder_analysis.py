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
                  documents the employee chose to share. Two interpreters
                  (`settings.recorder_interpreter`): `jev` derives workflow
                  candidates from the observed transfers, loops and stretches in
                  code and asks TypeSafe Jev typed questions about each, so every
                  item is born citing its evidence; `chat` asks the prose model to
                  write items and drops any that name an app not in `observed`.
  questions       focused prompts the employee answers before publishing.

The handler is idempotent per run: a retry recomputes the same draft and never
publishes anything. Publication is a separate, human action on the API.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from taskmining.state import app_role
from vista import documents
from vista.agents.jev import NONE, Judgment, choice, judge, noul, score
from vista.agents.llm import Prompt, chat, strip_fences
from vista.agents.runtime import run_phase
from vista.automation.schemas import PlanGraph
from vista.config import settings
from vista.db import tenant_session
from vista.models.platform import Job
from vista.models.tenant import AgentRun, AgentRunEvent, RecorderReport, RecorderSubmission

IDLE_GAP_S = 120  # a focus span ends when nothing happens for this long
STRETCH_GAP_S = 30  # work stretches are separated by gaps of at least this long
TRANSFER_WINDOW_S = 120  # copy in app A → paste in app B within this window counts as a transfer
MAX_QUESTIONS = 6
MAX_DOCUMENT_EXCERPT = 1200
COVERAGE_EXCLUDED = ["window_title", "url", "typed_text", "clipboard", "screenshots", "video", "raw_events"]
DETAIL_SAMPLE = 120  # characters kept of a typed run or a transferred clipboard value
DETAIL_TOP = 5  # titles / pages / files / typed samples kept per application
TYPED_RUN_GAP_S = 5.0  # keystrokes further apart than this start a new typed run


def _page(url: str) -> str:
    """A URL without its query string, so a page is one entry however it was parameterised."""
    return url.split("?", 1)[0].split("#", 1)[0][:DETAIL_SAMPLE]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


# ---- observed facts (pure code) -------------------------------------------------


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _clock(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%H:%M")


def observe(events: list[dict], manifest: dict) -> dict:
    """Deterministic facts from metadata-only events. Input rows are
    {timestamp, event_type, app, count}; output is JSON-serialisable."""
    rows = sorted(
        (
            {
                "t": _ts(e["timestamp"]),
                "type": e["event_type"],
                "app": str(e["app"])[:128],
                "n": int(e.get("count") or 1),
                # Present only under activity-full-v1 (see recorder_uploads.DETAIL_FIELDS).
                "title": str(e.get("window_title") or "").strip()[:255],
                "url": str(e.get("url") or "").strip()[:2048],
                "text": str(e.get("text") or "")[:4000],
            }
            for e in events
        ),
        key=lambda r: r["t"],
    )
    per_app: dict[str, dict] = defaultdict(
        lambda: {"active_s": 0.0, "events": 0, "clicks": 0, "keys": 0, "typing_runs": 0, "copies": 0, "pastes": 0, "spans": 0}
    )
    # A typing run is one field's worth of keys (gap-separated); it is the unit of work, the
    # keystroke count is only how long the value was. Runs are counted with or without text.
    key_app: str | None = None
    key_t: datetime | None = None
    # Detail, when the upload carried it: what was on screen and what moved, per app and per transfer.
    titles: dict[str, Counter] = defaultdict(Counter)
    pages: dict[str, Counter] = defaultdict(Counter)
    files: dict[str, Counter] = defaultdict(Counter)
    typed: dict[str, list[str]] = defaultdict(list)
    samples: dict[tuple[str, str], list[str]] = defaultdict(list)
    run_app: str | None = None
    run_text = ""
    run_t: datetime | None = None

    def flush_run() -> None:
        nonlocal run_app, run_text, run_t
        if run_app is not None and len(run_text.strip()) >= 3:
            typed[run_app].append(run_text.strip()[:DETAIL_SAMPLE])
            if run_stretch is not None:
                run_stretch["typed"][run_app].append(run_text.strip()[:DETAIL_SAMPLE])
        run_app, run_text, run_t = None, "", None

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
    run_stretch: dict | None = None  # the stretch a typed run started in

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
        if row["type"] == "file":
            if row["title"]:
                files[app][row["title"]] += 1  # a file event names the document, not a window
        elif row["title"]:
            titles[app][row["title"]] += 1
        if row["url"]:
            pages[app][_page(row["url"])] += 1
        # Typed text arrives one keystroke per event; join a run in one app into a sample.
        is_keystroke = row["type"] == "key" and row["text"] and len(row["text"]) <= 2
        if not is_keystroke or run_app != app or (run_t is not None and (t - run_t).total_seconds() > TYPED_RUN_GAP_S):
            flush_run()
        new_run = False
        if row["type"] == "key":
            if key_app != app or key_t is None or (t - key_t).total_seconds() > TYPED_RUN_GAP_S:
                stats["typing_runs"] += 1
                new_run = True
            key_app, key_t = app, t
        else:
            key_app, key_t = None, None
        gap = (t - previous["t"]).total_seconds() if previous else 0.0
        idle = previous is not None and gap > IDLE_GAP_S
        if idle:
            close_span(previous["t"])  # nothing happened: the span ends at the last event
        if stretch is None or gap > STRETCH_GAP_S:
            if stretch is not None:
                stretches.append(stretch)
            stretch = {
                "start": t,
                "end": t,
                "events": 0,
                "interactions": 0,
                "switches": 0,
                "apps": Counter(),
                # The small committed effects inside the stretch — what `workflow_candidates` calls tasks.
                "moves": Counter(),  # (from app, to app) → pastes within the transfer window, same app included
                "moved": defaultdict(list),  # (from, to) → clipboard samples, when shared
                "keys": Counter(),  # app → keystrokes
                "typed": defaultdict(list),  # app → typed runs, when shared
                "files": defaultdict(list),  # app → files opened, when shared
                "clicks": Counter(),  # app → clicks
            }
        stretch["end"] = t
        stretch["events"] += row["n"]
        # Interactions count a typing run once, so a long value does not make a stretch look busier.
        if row["type"] != "key":
            stretch["interactions"] += row["n"]
        elif new_run:
            stretch["interactions"] += 1
        stretch["apps"][app] += row["n"]
        if is_keystroke:
            if run_app is None:
                run_stretch = stretch
            run_app, run_t = app, t
            run_text += row["text"]
        if row["type"] == "click":
            stats["clicks"] += row["n"]
            stretch["clicks"][app] += row["n"]
        elif row["type"] == "key":
            stats["keys"] += row["n"]
            stretch["keys"][app] += row["n"]
        elif row["type"] == "copy":
            stats["copies"] += row["n"]
            last_copy = row
        elif row["type"] == "paste":
            stats["pastes"] += row["n"]
            if last_copy is not None and 0 <= (t - last_copy["t"]).total_seconds() <= TRANSFER_WINDOW_S:
                moved = (last_copy["text"] or row["text"]).strip()
                stretch["moves"][(last_copy["app"], app)] += 1
                if moved:
                    stretch["moved"][(last_copy["app"], app)].append(moved[:DETAIL_SAMPLE])
                if last_copy["app"] != app:
                    transfers[(last_copy["app"], app)] += 1
                    transfer_latency[(last_copy["app"], app)].append((t - last_copy["t"]).total_seconds())
                    if moved:
                        samples[(last_copy["app"], app)].append(moved[:DETAIL_SAMPLE])
                else:
                    internal_pastes += 1
        elif row["type"] == "file" and row["title"] and row["title"] not in stretch["files"][app]:
            stretch["files"][app].append(row["title"])
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
    flush_run()

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
    for entry in apps:
        name = entry["app"]
        for key, counter in (("titles", titles), ("pages", pages), ("files", files)):
            if counter[name]:
                entry[key] = [value for value, _ in counter[name].most_common(DETAIL_TOP)]
        if typed[name]:
            entry["typed"] = _dedupe(typed[name])[:DETAIL_TOP]
    transfer_rows = [
        {"from": a, "to": b, "count": n, "mean_latency_s": round(sum(transfer_latency[(a, b)]) / len(transfer_latency[(a, b)]), 1)}
        for (a, b), n in transfers.most_common(20)
    ]
    for tr in transfer_rows:
        moved = _dedupe(samples[(tr["from"], tr["to"])])
        if moved:
            tr["samples"] = moved[:3]
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
        "transfers": transfer_rows,
        "internal_pastes": internal_pastes,
        "loops": [{"between": [a, b], "count": n} for (a, b), n in loops.most_common(10)],
        "stretches": [
            {
                "id": f"s{i + 1}",
                "start": s["start"].isoformat(),
                "end": s["end"].isoformat(),
                "duration_s": round((s["end"] - s["start"]).total_seconds(), 1),
                "events": s["events"],
                "interactions": s["interactions"],
                "switches": s["switches"],
                "apps": [a for a, _ in s["apps"].most_common(6)],
                "tasks": _stretch_tasks(s),
            }
            for i, s in enumerate(stretches)
        ],
        "session": {
            "started_at": manifest["started_at"],
            "ended_at": manifest["ended_at"],
            "active_seconds": manifest.get("active_seconds"),
        },
    }


def _stretch_tasks(s: dict) -> list[dict]:
    """The small committed effects inside one stretch, in a fixed order: every copy→paste (same
    application included), every application typed into, every file opened. A stretch with none
    of those still did something, so it keeps one `activity` task per application clicked in.
    Counts and samples describe the task; its identity is (kind, apps) only."""
    tasks: list[dict] = []
    for (src, dst), n in sorted(s["moves"].items(), key=lambda kv: (-kv[1], kv[0])):
        task = {"kind": "transfer", "from": src, "to": dst, "count": n}
        if s["moved"][(src, dst)]:
            task["samples"] = _dedupe(s["moved"][(src, dst)])[:3]
        tasks.append(task)
    for app, n in sorted(s["keys"].items(), key=lambda kv: (-kv[1], kv[0])):
        task = {"kind": "entry", "app": app, "count": n}
        if s["typed"][app]:
            task["samples"] = _dedupe(s["typed"][app])[:3]
        tasks.append(task)
    for app in sorted(s["files"]):
        for name in s["files"][app][:DETAIL_TOP]:
            tasks.append({"kind": "open", "app": app, "file": name, "count": 1})
    if not tasks:
        for app, n in sorted(s["clicks"].items(), key=lambda kv: (-kv[1], kv[0])) or [(a, 0) for a, _ in s["apps"].most_common(1)]:
            tasks.append({"kind": "activity", "app": app, "count": n})
    return tasks


def has_detail(observed: dict) -> bool:
    """Whether the upload carried titles, pages, typed text or clipboard contents."""
    return any(k in a for a in observed.get("apps", []) for k in ("titles", "pages", "files", "typed")) or any(
        "samples" in t for t in observed.get("transfers", [])
    )


def coverage_for(observed: dict, document_count: int, sharing_policy: str = "activity-metadata-v1") -> dict:
    full = sharing_policy == "activity-full-v1"
    return {
        "sharing_policy": sharing_policy,
        "fields": ["timestamp", "event_type", "app", "count"] + (["window_title", "url", "element", "text"] if full else []),
        "excluded": ["screenshots", "video"] if full else COVERAGE_EXCLUDED,
        "events": observed["events"],
        "apps": len(observed["apps"]),
        "documents": document_count,
        "note": (
            "Window titles, pages, typed text and clipboard contents were shared for this session, so what "
            "moved between applications is observable; screenshots and video stayed on the device."
            if full
            else "Activities are known only at application level; documents, records and web pages the employee "
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


# ---- workflow candidates (code) → typed judgments (Jev) → interpretation ----------
#
# The chat interpretation above asks a model to *write* workflows and then drops what it
# invented. This path never lets it invent: code derives every candidate from the observed
# stretches of work and the tasks inside them, so each one is born citing its evidence, and
# Jev only answers typed questions about each — is it recurring, of what kind, how mechanical,
# and whether only the employee can say. Jev labels; it never filters. A candidate it is
# unsure about is still a workflow on the report, marked as such, because the employee and
# the FDE are the gates that decide what becomes an automation. Wording and thresholds live
# here, not in the model, so changing a threshold never re-runs inference.

MAX_CANDIDATES = 20
WORKFLOW_THRESHOLD = 0.5  # p(recurring unit of work) at which a workflow is marked `likely` rather than `unsure`
AUTOMATION_THRESHOLD = 2.0  # mechanical score (0..3) needed to propose automation
QUESTION_THRESHOLD = 0.6  # p(only the employee can say what it is) needed to ask

KINDS = {
    "data_transfer": "Values are copied out of one application and entered into another.",
    "lookup_and_enter": "Something is looked up in one application to decide or fill what is entered in another.",
    "reconciliation": "Two applications are compared back and forth to check that they agree.",
    "communication": "Messages or email are read or written around work in another application.",
    "review_approval": "Something is examined in one application and then approved, filed or forwarded in another.",
    "data_entry": "Values are typed or pasted into one application, record after record.",
    "document_work": "A document, sheet or page is read, edited or filed within one application.",
    NONE: "None of these describes it, or it is too little to tell what the work is.",
}
SINGLE_APP_KINDS = ("data_entry", "document_work")  # the only kinds a one-application candidate is offered
KIND_NAMES = {
    "data_transfer": "Data transfer",
    "lookup_and_enter": "Look up and enter",
    "reconciliation": "Reconciliation",
    "communication": "Communication loop",
    "review_approval": "Review and approval",
    "data_entry": "Data entry",
    "document_work": "Document work",
}
UNCLEAR_NAME = "Unclear work"
MECHANICAL = [
    "Every step needs the employee's judgment; nothing repeats the same way twice.",
    "Mostly judgment, with a few steps that repeat the same way each time.",
    "Mostly the same steps every time, with an occasional decision or exception.",
    "The same mechanical sequence every time: the same fields, the same order, no decision.",
]
TASK_ORDER = {"transfer": 0, "entry": 1, "open": 2, "activity": 3}


def _kinds_for(c: dict) -> dict[str, str]:
    """The kinds Jev may choose for one candidate: everything for work across applications,
    only the single-application kinds (and none) for work inside one."""
    if len(c["apps"]) >= 2:
        return dict(KINDS)
    return {k: KINDS[k] for k in (*SINGLE_APP_KINDS, NONE)}


def _pair(c: dict) -> tuple[str, str]:
    """(source, destination) applications of a candidate; one application plays both parts."""
    apps = c["apps"]
    return apps[0], apps[1] if len(apps) > 1 else apps[0]


def _transfer_count(c: dict) -> int:
    """Pastes moved between the candidate's two applications across all its stretches, or its stretch count."""
    a, b = _pair(c)
    return sum(t["count"] for t in c["tasks"] if t["kind"] == "transfer" and (t["from"], t["to"]) == (a, b)) or c["count"]


def task_name(t: dict) -> str:
    """One templated line per task, from the facts only."""
    n = t.get("count") or 0
    times = f" {n}×" if n > 1 else ""
    sample = f" “{t['samples'][0]}”" if t.get("samples") else ""
    if t["kind"] == "transfer":
        where = f"within {t['from']}" if t["from"] == t["to"] else f"{t['from']} → {t['to']}"
        return f"Copy and paste {where}{times}{sample}"
    if t["kind"] == "entry":
        return f"Type into {t['app']}" + (f":{sample}" if sample else f" ({n} keystroke{'s' if n != 1 else ''})")
    if t["kind"] == "open":
        return f"Open {t['file']} in {t['app']}"
    return f"Work in {t['app']}" + (f" ({n} click{'s' if n != 1 else ''})" if n else "")


def _task_key(t: dict) -> tuple:
    """A task's identity: kind and applications (and file), never its counts or samples."""
    if t["kind"] == "transfer":
        return ("transfer", t["from"], t["to"])
    if t["kind"] == "open":
        return ("open", t["app"], t["file"])
    return (t["kind"], t["app"])


def _evidence_text(c: dict) -> str:
    about = c["about"]
    when = f"between {_clock(_ts(about['start']))} and {_clock(_ts(about['end']))}"
    tasks = "; ".join(t["name"] for t in c["tasks"][:4])
    if len(c["tasks"]) > 4:
        tasks += f"; and {len(c['tasks']) - 4} more"
    seen = f", seen in {c['count']} stretches of work" if c["count"] > 1 else ""
    return f"{tasks[0].upper() + tasks[1:]} {when}{seen}"


def workflow_candidates(observed: dict) -> list[dict]:
    """Every stretch of work is a candidate workflow, made of the tasks observed inside it —
    one application or several, one paste or forty. Stretches with the same set of tasks are
    one candidate whose `count` is how many times it was seen. Nothing is dropped for being
    small or one-off: Jev labels each and the report shows them all, so a single copy from a
    document into a sheet is visible instead of silently below a threshold."""
    groups: dict[tuple, dict] = {}
    for s in observed["stretches"]:
        if not s["tasks"]:
            continue
        key = tuple(sorted(_task_key(t) for t in s["tasks"]))
        g = groups.setdefault(key, {"stretches": [], "tasks": {}, "apps": Counter(), "events": 0, "interactions": 0, "switches": 0})
        g["stretches"].append(s)
        g["events"] += s["events"]
        g["interactions"] += s.get("interactions", s["events"])
        g["switches"] += s["switches"]
        for rank, app in enumerate(s["apps"]):
            g["apps"][app] += len(s["apps"]) - rank
        for t in s["tasks"]:
            merged = g["tasks"].get(_task_key(t))
            if merged is None:
                merged = g["tasks"][_task_key(t)] = {k: v for k, v in t.items() if k != "samples"}
            else:
                merged["count"] += t["count"]
            if t.get("samples"):
                merged["samples"] = _dedupe(merged.get("samples", []) + t["samples"])[:3]
    found = []
    for g in groups.values():
        tasks = sorted(g["tasks"].values(), key=lambda t: (TASK_ORDER[t["kind"]], -t["count"]))
        for t in tasks:
            t["name"] = task_name(t)
        first, last = g["stretches"][0], g["stretches"][-1]
        c = {
            "pattern": tasks[0]["kind"],
            "apps": [a for a, _ in g["apps"].most_common(3)],
            "count": len(g["stretches"]),
            "tasks": tasks,
            "about": {
                "stretches": [s["id"] for s in g["stretches"]],
                "start": first["start"],
                "end": last["end"],
                "switches": g["switches"],
                "events": g["events"],
                "interactions": g["interactions"],
                "tasks": [{k: v for k, v in t.items() if k != "name"} for t in tasks],
            },
        }
        c["evidence"] = _evidence_text(c)
        found.append(c)
    # Work that moved or entered something ranks above bare activity; then what recurred most.
    found.sort(key=lambda c: (TASK_ORDER[c["pattern"]], -c["count"], -c["about"]["events"], c["apps"]))
    return [{"id": f"c{i + 1}", **c} for i, c in enumerate(found[:MAX_CANDIDATES])]


MAX_SUMMARY = 2000


def judge_facts(observed: dict) -> dict:
    """The observed facts as Jev sees them. Keystrokes are how a value was entered, not how much
    work it was: the per-app `keys` count is dropped in favour of `typing_runs` (fields typed),
    and a stretch reports `interactions` (a typing run counts once) rather than raw `events`."""
    facts = {k: observed[k] for k in ("events", "apps", "switches", "transfers", "loops", "stretches", "session")}
    facts["apps"] = [{k: v for k, v in a.items() if k != "keys"} for a in observed["apps"]]
    facts["stretches"] = [{**{k: v for k, v in s.items() if k != "events"}} for s in observed["stretches"]]
    return facts


def judge_request(
    observed: dict, candidates: list[dict], docs: list[dict], answers: list[dict] | None = None, summary: str | None = None
) -> tuple[dict, dict]:
    """State and questions for one Jev call: four typed questions per candidate, all answered in parallel.

    `answers` are the employee's own replies to the report's questions (question, answer). They
    are facts about the session that no metadata carries — what moved and why — and once given
    they are re-judged with, never only stored beside, the observed patterns. `summary` is what
    the employee typed at Start about the session's purpose: context for every judgment, never
    a value the graph or a run could use."""
    facts = judge_facts(observed)
    answered = [{"question": a["question"], "answer": a["answer"]} for a in (answers or []) if a.get("answer")]
    stated = " ".join(str(summary or "").split())[:MAX_SUMMARY]
    state = {
        "context": (
            "Record of one employee's work session: application names, timing, switches and copy→paste transfers, "
            "with the window titles and pages seen in each application (`titles`, `pages`, `files`), samples of "
            "what was typed there (`typed`) and of what each transfer moved (`samples`). Screenshots were not captured."
            if has_detail(observed)
            else "Metadata-only record of one employee's work session: application names, timing, switches and "
            "copy→paste transfers. Window titles, URLs, typed text and screenshots were not shared."
        )
        + " `candidates` are stretches of work, each listing the `tasks` observed inside it; one may be a single task.",
        "observed": facts,
        "shared_documents": [{"filename": d["filename"], "summary": d["summary"], "excerpt": d["excerpt"]} for d in docs],
        "candidates": [
            {**{k: c[k] for k in ("id", "pattern", "apps", "count", "evidence")}, "tasks": [t["name"] for t in c["tasks"]]}
            for c in candidates
        ],
    }
    state["context"] += (
        " `typing_runs` is how many fields were typed into; how many keys that took says nothing about whether the work is mechanical."
    )
    if stated:
        state["employee_summary"] = stated
        state["context"] += (
            " `employee_summary` is what the employee said the session was for before recording it, in their "
            "own words; read every pattern in that light."
        )
    if answered:
        state["employee_answers"] = answered
        state["context"] += (
            " `employee_answers` are the employee's own replies about this session: what they were doing, what moved "
            "between the applications and whether they repeat it. Treat them as the most direct evidence there is."
        )
    with_answers = " and the employee's own account in `employee_answers`" if answered else ""
    if stated:
        with_answers += " and the session's stated purpose in `employee_summary`"
    questions: dict[str, dict] = {}
    for c in candidates:
        ref = f"candidate `{c['id']}` in `candidates` ({' and '.join(c['apps'])}; {c['evidence'][0].lower() + c['evidence'][1:]})"
        questions[f"{c['id']}_workflow"] = noul(
            f"Is {ref} a recurring unit of work — something this employee does the same way again and again — "
            f"rather than incidental activity? Judge from the tasks and the timing{with_answers}.",
            {
                "true": "A repeatable task with a purpose.",
                "false": "Incidental, one-off, or just where the employee's attention happened to go.",
            },
        )
        questions[f"{c['id']}_kind"] = choice(
            f"What kind of work is {ref}? Judge from the tasks, the timing, any `shared_documents`{with_answers}.", _kinds_for(c)
        )
        questions[f"{c['id']}_mechanical"] = score(
            f"How mechanical is {ref}: how much of it is the same steps in the same order, with no decision to make?"
            f"{' Weigh what the employee said in `employee_answers`.' if answered else ''}",
            MECHANICAL,
        )
        questions[f"{c['id']}_ask"] = noul(
            f"Could only the employee say what {ref} actually is — what the values are, where they come from, and why?"
            f"{' They have already answered in `employee_answers`; ask again only if that leaves it unclear.' if answered else ''}",
            {
                "true": "The metadata leaves the task itself unknown; ask before proposing anything.",
                "false": "The tasks, the shared documents and any answers already make the work clear enough to describe.",
            },
        )
    return state, questions


def _workflow_name(kind: str, apps: list[str]) -> str:
    if kind == NONE:
        return f"{UNCLEAR_NAME}: {', '.join(apps[:3])}"
    if kind in SINGLE_APP_KINDS:
        return f"{KIND_NAMES[kind]}: {apps[0]}"
    joiner = " → " if kind == "data_transfer" else " ↔ "
    return f"{KIND_NAMES[kind]}: {joiner.join(apps[:2])}"


def _question_for(kind: str, c: dict) -> str:
    (a, b), n = _pair(c), _transfer_count(c)
    when = f"between {_clock(_ts(c['about']['start']))} and {_clock(_ts(c['about']['end']))}"
    return {
        "data_transfer": (
            f"You moved values from {a} into {b} about {n} time{'s' if n != 1 else ''}. "
            "What is being re-keyed, and is there a file or export it could come from instead?"
        ),
        "lookup_and_enter": f"You went between {a} and {b}. What do you look up in one before entering it in the other?",
        "reconciliation": (
            f"You went back and forth between {a} and {b}. What are you checking agrees between them, and what happens when it doesn't?"
        ),
        "communication": (
            f"Around your work in {b}, you kept returning to {a}. "
            "What are you sending or receiving there, and who decides what happens next?"
        ),
        "review_approval": (
            f"Between {a} and {b}: what do you check in the first before acting in the second, and does anyone else have to approve it?"
        ),
        "data_entry": f"You entered values in {a} {when}. What are they, where do they come from, and how often do you enter them?",
        "document_work": f"You worked in {a} {when}. Which document or page was it, what did you do to it, and is that a regular task?",
        NONE: f"You worked in {', '.join(c['apps'][:3])} {when}. What were you doing, and is it something you repeat?",
    }[kind]


# ---- what to do about a judged workflow (templated in code; nothing generated) ----------
#
# Every judged workflow carries `actions`: a numbered checklist for the FDE, filled from
# the facts, and — when Jev scored it mechanical enough — a prefilled `WorkflowDefinition`
# the workspace can turn into a draft version with one button. The draft is a starting
# point that still needs a human edit and an admin decision; nothing here executes.

TOOLS = {
    "data_transfer": ["read_source_records", "map_fields", "write_destination_records", "compare_with_manual_entry"],
    "lookup_and_enter": ["lookup_reference", "read_source_records", "write_destination_records", "compare_with_manual_entry"],
    "reconciliation": ["read_source_records", "read_destination_records", "match_records", "report_differences"],
    "communication": ["read_messages", "extract_requests", "create_task", "draft_reply"],
    "review_approval": ["read_source_records", "check_against_rules", "route_for_approval", "record_decision"],
    "data_entry": ["read_source_records", "map_fields", "write_destination_records", "compare_with_manual_entry"],
    "document_work": ["read_source_records", "extract_requests", "write_destination_records", "compare_with_manual_entry"],
}
DRAFT_LIMITS = {"max_steps": 10, "max_runtime_seconds": 300, "max_cost_usd": "1.00"}


def graph_for(plan: dict | None, apps: list[str]) -> dict | None:
    """The recording's plan graph, if it visits every app this candidate is about. The graph is
    attached whole: a candidate is a *pattern* the reviewer noticed, the graph is the *path* the
    employee took, and the FDE prunes edges in the workspace before approving anything."""
    if not plan or not plan.get("nodes"):
        return None
    roles = {n["app_role"] for n in plan["nodes"]}
    if all(app_role(app) in roles for app in apps):
        return plan
    return None


def draft_definition(kind: str, c: dict, docs: list[dict], plan: dict | None = None) -> dict:
    """A `WorkflowDefinition` (automation/schemas.py) prefilled from the kind and the apps."""
    (a, b), n = _pair(c), _transfer_count(c)
    goal = {
        "data_transfer": (
            f"Move the values the employee re-keys from {a} into {b} (about {n} times per session) without manual entry, "
            "and list anything that could not be mapped for a person to handle."
        ),
        "lookup_and_enter": (
            f"Look up the reference the employee finds in {a} and fill the matching entry in {b}, leaving unmatched cases for a person."
        ),
        "reconciliation": (
            f"Compare the records the employee checks between {a} and {b} and report every difference, without changing either side."
        ),
        "communication": (
            f"Read the requests arriving in {a} that drive work in {b}, turn each into a task, and draft a reply for a person to send."
        ),
        "review_approval": (
            f"Check what the employee examines in {a} against the rules that decide it, and route the result for approval in {b}."
        ),
        "data_entry": (
            f"Enter the values the employee keys into {a} from their source without manual entry, "
            "and list anything that could not be mapped for a person to handle."
        ),
        "document_work": (
            f"Do the routine edits the employee makes to the document in {a} the same way each time, "
            "and leave anything that needs a decision for a person."
        ),
    }[kind]
    criteria = {
        "data_transfer": [
            f"Every value written to {b} equals the corresponding source value from {a}",
            f"No record is created in {b} that the employee would not have created by hand",
            "Anything that could not be mapped is listed for a person instead of guessed",
        ],
        "lookup_and_enter": [
            f"Every entry in {b} is filled from the {a} record the employee would have chosen",
            "A lookup with no match or more than one match is left for a person, not guessed",
        ],
        "reconciliation": [
            f"Every difference between {a} and {b} in the sample is reported with both values",
            "Neither system is changed",
        ],
        "communication": [
            f"Every request in the {a} sample becomes exactly one task with the right owner",
            "No reply is sent without a person pressing send",
        ],
        "review_approval": [
            f"Every case in the sample gets the same decision the employee gave it in {a}",
            "Anything the rules do not cover is routed to a person with the reason",
        ],
        "data_entry": [
            f"Every value written to {a} equals the corresponding source value",
            f"No record is created in {a} that the employee would not have created by hand",
            "Anything that could not be mapped is listed for a person instead of guessed",
        ],
        "document_work": [
            f"The document in {a} ends in the state the employee would have left it in",
            "Nothing is deleted or sent without a person confirming it",
        ],
    }[kind]
    inputs = [f"{a} export or sample", f"{b} field list"] if a != b else [f"{a} source values or sample", f"{a} field list"]
    graph = graph_for(plan, c["apps"])
    if graph is not None:
        # every value the employee typed is a declared input the FDE binds before a run
        produced = {name for e in graph["edges"] for name in e.get("produces", [])}
        slots = sorted({e["slot"] for e in graph["edges"] if e.get("slot") and e["slot"] not in produced})
        if len(inputs) + len(slots) > 30:
            graph = None
        else:
            inputs += slots
    inputs += [f"shared document: {d['filename']}" for d in docs[:5]]
    definition = {
        "goal": goal,
        "required_inputs": list(dict.fromkeys(i[:255] for i in inputs))[:30],
        "allowed_tools": TOOLS[kind],
        "success_criteria": criteria,
        "environment": "sandbox",
        "limits": dict(DRAFT_LIMITS),
    }
    if graph is not None:
        definition["graph"] = graph
    return definition


def instructions_for(kind: str, c: dict, docs: list[dict], question: str | None, automation: bool) -> list[str]:
    """Specific next steps for the FDE, in order. Each names the apps, the counts and the documents involved."""
    a, b = _pair(c)
    tasks = "; ".join(t["name"][0].lower() + t["name"][1:] for t in c["tasks"][:4])
    confirm = f"what actually moves between {a} and {b}" if a != b else f"what the work in {a} is"
    steps = [
        f"Confirm with the employee {confirm}"
        + (f" — the report asks: “{question}”" if question else "")
        + ". Their answer on the published report is the baseline; do not proceed from the pattern alone."
    ]
    if kind == NONE:
        steps.append(
            f"The observed tasks — {tasks} — were too little to classify. Once the employee has said what they are, "
            "decide whether they belong to a workflow already on this report or are one of their own, and rename it."
        )
        steps.append("Nothing to automate yet: an unclear task is not a baseline. Revisit after the next recording of the same work.")
        return steps
    if docs:
        names = ", ".join(d["filename"] for d in docs[:3])
        steps.append(
            f"Check whether the shared document{'s' if len(docs) > 1 else ''} ({names}) {'are' if len(docs) > 1 else 'is'} the {a} source. "
            "If not, ask for one representative export."
        )
    else:
        steps.append(f"Ask for one representative export or sample from {a}: the fields, not the volume, are what matter.")
    steps.append(
        {
            "data_transfer": (
                f"List the fields in {b} that receive the values and map each to a column in the {a} source. "
                "Note any value the employee changes on the way (formats, codes, defaults)."
            ),
            "lookup_and_enter": (
                f"Write down what is looked up in {a} (the key) and which fields in {b} depend on it. "
                "Note what happens when the lookup finds nothing, or more than one match."
            ),
            "reconciliation": (
                f"Write down the matching rule the employee uses between {a} and {b} (which identifiers, which amounts) "
                "and what they do when the two disagree."
            ),
            "communication": (
                f"Sample a week of the messages in {a} that lead to work in {b}. "
                "Group them by what they ask for and note who decides the response."
            ),
            "review_approval": (
                f"Write down the rules the employee applies in {a} before acting in {b}, including who else must approve, and when."
            ),
            "data_entry": (
                f"List the fields in {a} that receive the values and where each value comes from (a document, a message, memory). "
                "Note any value the employee changes on the way (formats, codes, defaults)."
            ),
            "document_work": (
                f"Write down which document or page in {a} the work is on and the edits made to it each time, "
                "and which of them ever need a decision."
            ),
        }[kind]
    )
    steps.append(
        f"Measure the baseline from this session — {tasks} — then ask how often it recurs per week "
        "and how long one round takes, so an outcome can be compared to something."
    )
    if automation:
        steps.append(
            "Draft the workflow from the prefilled definition (button below): sandbox only, at most 10 steps and $1.00 per run. "
            "Edit the inputs and tools to match what you learned; the definition is a starting point, not a decision."
        )
        steps.append(
            "Run the draft against the sample and compare every result with what the employee entered by hand. "
            "Submit the version for an admin decision; execution stays unavailable until it is approved and eligible."
        )
    else:
        steps.append(
            "Not mechanical enough to automate yet: look for the smaller fix first — a template, a saved view, an export, or a field "
            "added to one system — and record what it changes against the measurement above. "
            "Revisit automation once the steps stop varying."
        )
    return steps


def apply_judgment(
    judgment: Judgment,
    candidates: list[dict],
    *,
    model: str,
    source: str,
    docs: list[dict] | None = None,
    plan: dict | None = None,
    answers: list[dict] | None = None,
) -> tuple[dict, list[dict]]:
    """Turn typed answers into the report's interpretation. Nothing here is generated:
    names, rationales, questions and next steps are templates over the candidate and
    the chosen labels, and confidence is the least certain judgment an item depends on.
    Every candidate becomes a workflow: `status` is `likely` when Jev rated it a recurring
    unit of work of a known kind, `unsure` otherwise — a label for the reader, not a filter.
    `answers` are the employee's replies that this judgment was made with; the summary
    quotes the first so the reader sees the task in the employee's words."""
    workflows, automation, questions, unsure = [], [], [], 0
    for c in candidates:
        p_workflow = judgment.noul(f"{c['id']}_workflow")
        kind, p_kind = judgment.choice(f"{c['id']}_kind")
        mechanical, p_mechanical = judgment.score(f"{c['id']}_mechanical")
        p_ask = judgment.noul(f"{c['id']}_ask")
        likely = p_workflow >= WORKFLOW_THRESHOLD and kind != NONE
        unsure += 0 if likely else 1
        confidence = round(min(p_workflow, p_kind), 2)
        name = _workflow_name(kind, c["apps"])
        automate = kind != NONE and mechanical >= AUTOMATION_THRESHOLD
        asked = _question_for(kind, c) if p_ask >= QUESTION_THRESHOLD or kind == NONE else None
        actions = {
            "instructions": instructions_for(kind, c, list(docs or []), asked, automate),
            "draft_definition": draft_definition(kind, c, list(docs or []), plan) if automate else None,
            "question": asked,
        }
        item = {
            "name": name,
            "kind": kind,
            "status": "likely" if likely else "unsure",
            "apps": c["apps"],
            "tasks": [t["name"] for t in c["tasks"]],
            "evidence": c["evidence"],
            "about": c["about"],
            "confidence": confidence,
            "candidate": c["id"],
            "actions": actions,
        }
        workflows.append(item)
        if automate:
            level = MECHANICAL[min(round(mechanical), len(MECHANICAL) - 1)]
            automation.append(
                {
                    "title": f"Automate {name[0].lower() + name[1:]}",
                    "rationale": f"{level} {KINDS[kind]}",
                    "apps": c["apps"],
                    "evidence": c["evidence"],
                    "about": c["about"],
                    "confidence": round(min(confidence, p_mechanical), 2),
                    "mechanical": round(mechanical, 2),
                    "candidate": c["id"],
                    "actions": actions,
                }
            )
        if asked:
            questions.append({"source": "model", "question": asked, "about": {"apps": c["apps"], "candidate": c["id"]}})
    summary = ""
    if candidates:
        likely_n = len(workflows) - unsure
        summary = (
            f"{len(workflows)} workflow{'s' if len(workflows) != 1 else ''} from {len(candidates)} "
            f"stretch{'es' if len(candidates) != 1 else ''} of work; {likely_n} judged likely recurring"
        )
        summary += f"; {len(automation)} look{'s' if len(automation) == 1 else ''} mechanical enough to automate."
    answered = [a for a in (answers or []) if a.get("answer")]
    if answered:
        quoted = str(answered[0]["answer"]).strip()[:200]
        summary = (summary + " " if summary else "") + (
            f"Read with {len(answered)} answer{'s' if len(answered) != 1 else ''} from the employee, who described it as: “{quoted}”."
        )
    interpretation = {
        "source": source,
        "model": model,
        "summary": summary,
        "workflows": workflows,
        "automation_candidates": automation,
        "rejected": 0,
        "unsure": unsure,
        "judged": len(candidates),
        "answered": len(answered),
    }
    if plan:
        interpretation["plan"] = {
            "states": len(plan["nodes"]),
            "moves": len(plan["edges"]),
            "trajectories": plan["trajectories"],
            "roles": sorted({n["app_role"] for n in plan["nodes"]}),
        }
    return interpretation, questions[:3]


@dataclass
class Interpretation:
    interpretation: dict
    questions: list[dict]
    model: str
    source: str
    input_tokens: int
    output_tokens: int
    event: dict  # interpreter-specific detail for the `model_call` event


def interpret_with_jev(
    observed: dict,
    docs: list[dict],
    judge_fn=judge,
    plan: dict | None = None,
    answers: list[dict] | None = None,
    summary: str | None = None,
) -> Interpretation:
    candidates = workflow_candidates(observed)
    answered = [a for a in (answers or []) if a.get("answer")]
    if not candidates:
        interpretation, questions = apply_judgment(
            Judgment(model="none", source="code"), [], model="none", source="code", plan=plan, answers=answered
        )
        return Interpretation(
            interpretation, questions, "none", "code", 0, 0, {"interpreter": "jev", "candidates": 0, "questions": 0, "rejected": 0}
        )
    state, questions = judge_request(observed, candidates, docs, answered, summary)
    judgment = judge_fn(state, questions)
    interpretation, model_questions = apply_judgment(
        judgment, candidates, model=judgment.model, source=judgment.source, docs=docs, plan=plan, answers=answered
    )
    event = {
        "interpreter": "jev",
        "candidates": len(candidates),
        "questions": len(questions),
        "rejected": interpretation["rejected"],
        "unsure": interpretation["unsure"],
        "employee_answers": len(answered),
    }
    return Interpretation(
        interpretation, model_questions, judgment.model, judgment.source, judgment.input_tokens, judgment.output_tokens, event
    )


def interpret_with_chat(observed: dict, docs: list[dict], llm=chat) -> Interpretation:
    phase = run_phase(prepare(observed, docs), parse, lambda output: [], llm=llm)
    interpretation, model_questions = apply_interpretation(phase.output, observed, model=phase.result.model, source=phase.result.source)
    event = {"interpreter": "chat", "parsed": phase.output is not None, "rejected": interpretation["rejected"]}
    r = phase.result
    return Interpretation(interpretation, model_questions, r.model, r.source, r.input_tokens, r.output_tokens, event)


def interpret(
    observed: dict, docs: list[dict], plan: dict | None = None, answers: list[dict] | None = None, summary: str | None = None
) -> Interpretation:
    if settings.recorder_interpreter == "jev":
        return interpret_with_jev(observed, docs, plan=plan, answers=answers, summary=summary)
    return interpret_with_chat(observed, docs)


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
        plan = None
        docs: list[dict] = []
        for artifact in row.manifest["artifacts"]:
            data = read_artifact(tenant_schema, row, artifact, expected=verified.get(artifact["id"]))
            seq = _emit(session, run_id, seq, "tool_call", {"tool": "read_artifact", "artifact": artifact["id"], "bytes": len(data)})
            if artifact["kind"] == "activity":
                activity = json.loads(data)
            elif artifact["kind"] == "plan":
                plan = PlanGraph.model_validate_json(data).model_dump(mode="json", exclude_none=True)
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
        # A re-run after the employee answered: their replies are evidence for this judgment.
        prior = session.scalar(select(RecorderReport).where(RecorderReport.submission_id == submission_id))
        answers = [{"question": q["question"], "answer": q["answer"]} for q in (prior.questions if prior else []) if q.get("answer")]
        result = interpret(observed, docs, plan, answers, summary=row.manifest.get("summary_text"))
        interpretation, model_questions = result.interpretation, result.questions
        seq = _emit(
            session,
            run_id,
            seq,
            "model_call",
            {
                "model": result.model,
                "source": result.source,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                **result.event,
            },
        )
        if result.source != "code":  # no candidates → no model call → nothing to meter
            record_usage(session, run, result.model, result.input_tokens, result.output_tokens)
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
        report.coverage = coverage_for(observed, len(docs), str(row.manifest.get("sharing_policy") or "activity-metadata-v1"))
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


def finding_rows(report: RecorderReport) -> list[dict]:
    """The published report's judged workflows as `findings` rows (keyword fields for
    `Finding(...)`), each citing the report, the candidate and the run, and carrying the
    employee's answer and the `actions` the workspace renders. Pure: persistence and
    dedupe are `recorder_uploads.record_findings`'s job. Items from the chat interpreter
    (no candidate) still become findings; they just cite less."""
    interpretation = report.interpretation or {}
    answers = {q["about"].get("candidate"): q.get("answer") for q in report.questions or [] if isinstance(q.get("about"), dict)}
    automation = {a.get("candidate"): a for a in interpretation.get("automation_candidates", []) if a.get("candidate")}
    rows = []
    for w in interpretation.get("workflows", []):
        cid = w.get("candidate")
        auto = automation.get(cid) if cid else None
        answer = answers.get(cid) if cid else None
        detail = [w.get("evidence") or ""]
        if w.get("tasks"):
            detail.append("Tasks: " + "; ".join(w["tasks"]) + ".")
        if w.get("kind") in KIND_NAMES:
            detail.append(KINDS[w["kind"]])
        pct = round((w.get("confidence") or 0) * 100)
        likely = w.get("status", "likely") == "likely"
        detail.append(
            (f"Judged a recurring workflow at {pct}%" if likely else f"Unsure it is a recurring workflow ({pct}%)")
            + (f"; mechanical {auto['mechanical']} of 3." if auto else "; not mechanical enough to automate yet.")
        )
        if answer:
            detail.append(f"Employee: {answer}")
        refs = [f"report:{report.id}"] + ([f"candidate:{cid}"] if cid else []) + ([f"run:{report.run_id}"] if report.run_id else [])
        actions = (auto or w).get("actions")
        rows.append(
            {
                "kind": "proposed_automation" if auto else "inefficiency",
                "title": auto["title"] if auto else w["name"],
                "detail": " ".join(p for p in detail if p),
                "evidence": {
                    "refs": refs,
                    "report_id": str(report.id),
                    "candidate": cid,
                    "about": w.get("about"),
                    "from_run": str(report.run_id) if report.run_id else None,
                    "apps": w.get("apps", []),
                    "kind": w.get("kind"),
                    "status": w.get("status"),
                    "tasks": w.get("tasks"),
                    "confidence": w.get("confidence"),
                    "mechanical": auto.get("mechanical") if auto else None,
                    "question": actions.get("question") if actions else None,
                    "answer": answer,
                    "actions": actions,
                },
                "finding_type": f"workflow.{w['kind']}" if w.get("kind") in KIND_NAMES else None,
            }
        )
    return rows


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
