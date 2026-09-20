"""Employee review of a recording: the model explains each stretch of work, the
employee approves, fixes or explains it. Mirrors src/recorder/src/explain.js so
the desktop app and the backend agree on statuses and decision rules.

Item lifecycle:
  pending   — waiting for the worker to ask the model
  proposed  — confident explanation, waiting for Approve / Fix
  unsure    — below threshold, the employee must explain
  approved  — employee accepted the AI label as-is
  fixed     — employee corrected a confident explanation
  explained — employee explained an unsure/failed stretch
  failed    — the model call failed; treated like unsure
"""

import json
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from vista.config import settings
from vista.models.tenant import RecordingReviewItem

CONFIDENCE_THRESHOLD = 0.88
SESSION_ID = "session"
OPEN_STATUSES = {"pending", "proposed", "unsure", "failed"}
RESOLVED_STATUSES = {"approved", "fixed", "explained"}

Text = Annotated[str, Field(max_length=4096)]
ItemID = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]

SYSTEM = """You are Vista, a process analyst helping a small company understand how its employees actually work.
You are shown what a desktop recorder observed during one stretch of an employee's day (or the whole session), plus anything the employee has already said.
Explain, in plain language, what the employee was most likely doing and why. Then rate how sure you are.
Rules:
- "label": 3 to 8 words naming the task, as an analyst would write it on a process map (e.g. "Enter vendor bills in QuickBooks").
- "explanation": 1 to 3 sentences, plain language, no jargon, only what the evidence supports. Say what was done, from what to what, and how it fits the day.
- "confidence": a number from 0 to 1. Be honest: 0.9+ only when the window titles, interaction pattern and copy/paste flows leave little doubt about the task; 0.5-0.8 when the app is clear but the purpose is not; below 0.5 when you are guessing.
- "unclear": short list of what the screen cannot tell you (why, what triggered it, what happened off-screen, what decision was made).
- "questions": 2 to 4 short questions the employee could answer in one sentence each to resolve "unclear". Never ask for passwords, personal data or named customers.
Respond as JSON: {"label": "...", "explanation": "...", "confidence": 0.0, "unclear": ["..."], "questions": ["..."]}"""


class SectionMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Text = ""
    app: Text = ""
    title: Text = ""
    start: datetime | None = None
    end: datetime | None = None
    seconds: float = Field(0, ge=0, allow_inf_nan=False)
    counts: dict[str, int] = Field(default_factory=dict, max_length=30)
    whole: bool = False


class SectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: ItemID
    description: str = Field(min_length=1, max_length=20_000)
    section: SectionMeta = Field(default_factory=SectionMeta)


class SectionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[SectionIn] = Field(min_length=1, max_length=40)
    force: bool = False  # redo open items whose explanation already exists


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: Text
    a: Text


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["approve", "fix", "explain"]
    label: Text = ""
    note: Text = ""
    answers: list[Answer] = Field(default_factory=list, max_length=10)


def status_for(confidence: float, threshold: float = CONFIDENCE_THRESHOLD) -> str:
    return "proposed" if confidence >= threshold else "unsure"


def _str_list(value, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip()[:300] for v in value if str(v).strip()][:limit]


def parse_explanation(text: str | None) -> dict:
    """Tolerant parse of the model's JSON; a prose answer becomes a zero-confidence
    explanation whose question marks are surfaced as questions."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("```")[1].removeprefix("json").strip()
    try:
        j = json.loads(s)
        if not isinstance(j, dict):
            raise ValueError
        confidence = float(j.get("confidence", 0))
        return {
            "label": str(j.get("label", "")).strip()[:120],
            "explanation": str(j.get("explanation", "")).strip()[:1200],
            "confidence": min(1.0, max(0.0, confidence)) if confidence == confidence else 0.0,
            "unclear": _str_list(j.get("unclear"), 6),
            "questions": _str_list(j.get("questions"), 4),
        }
    except (ValueError, TypeError):
        questions = [ln.lstrip(" -*0123456789.)").strip() for ln in s.split("\n")]
        return {
            "label": "",
            "explanation": s[:300],
            "confidence": 0.0,
            "unclear": [],
            "questions": [q for q in questions if q.endswith("?")][:4],
        }


def explain(prompt: str) -> tuple[str, dict, int, int]:
    """One model call for one section. Returns (model, parsed, input_tokens, output_tokens).
    Without an API key a deterministic stub marks the item unsure so the employee
    is asked instead of being shown an invented explanation."""
    if not settings.openai_api_key:
        return "stub-model-v0", parse_explanation(None) | {"explanation": "No model configured; please explain this stretch."}, 0, 0
    resp = settings.openai_client().chat.completions.create(
        model=settings.openai_model,
        max_tokens=500,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        extra_body=settings.openai_extra_body(),
    )
    usage = resp.usage
    return (
        resp.model,
        parse_explanation(resp.choices[0].message.content),
        usage.prompt_tokens if usage else 0,
        usage.completion_tokens if usage else 0,
    )


def apply_decision(item: RecordingReviewItem, body: DecisionIn, user_id) -> None:
    """Same rules as explain.js applyDecision: approve only a confident proposal;
    fix needs a label; explain needs a label or a note."""
    label, note = body.label.strip(), body.note.strip()
    if body.action == "approve":
        if item.status != "proposed":
            raise ValueError("only a confident explanation can be approved")
        status, final_label, final_note = "approved", item.label, item.explanation
    elif body.action == "fix":
        if not label:
            raise ValueError("a corrected label is required")
        status, final_label, final_note = "fixed", label, note
    else:
        if not label and not note:
            raise ValueError("an explanation is required")
        status, final_label, final_note = "explained", label or note[:80], note
    qa = [{"q": a.q.strip(), "a": a.a.strip()} for a in body.answers if a.q.strip() and a.a.strip()]
    item.status = status
    item.decision = body.action
    item.final_label = final_label
    item.final_note = " · ".join([final_note, *[f"{x['q']} {x['a']}" for x in qa]]).strip(" ·")
    item.answers = qa
    item.resolved_by = user_id
    item.resolved_at = item.updated_at = datetime.now(UTC)


def public_item(item: RecordingReviewItem) -> dict:
    return {
        "id": item.item_id,
        "section": item.section,
        "status": item.status,
        "label": item.label,
        "explanation": item.explanation,
        "confidence": item.confidence,
        "unclear": item.unclear,
        "questions": item.questions,
        "threshold": item.threshold,
        "model": item.model,
        "error": item.error,
        "at": item.explained_at,
        "decision": item.decision,
        "final_label": item.final_label,
        "final_note": item.final_note,
        "answers": item.answers,
        "resolved_at": item.resolved_at,
    }


def review_summary(items: list[RecordingReviewItem], threshold: float = CONFIDENCE_THRESHOLD) -> dict:
    """What the analyst (and the review banner) needs at a glance."""
    sections = [i for i in items if i.item_id != SESSION_ID]
    s = {
        "total": len(sections),
        "threshold": threshold,
        "pending": 0,
        "awaiting": 0,
        "unclear": 0,
        "approved": 0,
        "fixed": 0,
        "explained": 0,
        "failed": 0,
        "unclear_ids": [],
        "awaiting_ids": [],
    }
    for i in sections:
        if i.status == "proposed":
            s["awaiting"] += 1
            s["awaiting_ids"].append(i.item_id)
        elif i.status == "unsure":
            s["unclear"] += 1
            s["unclear_ids"].append(i.item_id)
        elif i.status == "failed":
            s["failed"] += 1
            s["unclear_ids"].append(i.item_id)
        elif i.status in s:
            s[i.status] += 1
    s["open"] = s["awaiting"] + s["unclear"] + s["failed"]
    s["resolved"] = s["approved"] + s["fixed"] + s["explained"]
    return s


def public_review(items: list[RecordingReviewItem]) -> dict:
    threshold = items[0].threshold if items else CONFIDENCE_THRESHOLD
    session = next((i for i in items if i.item_id == SESSION_ID), None)
    return {
        "threshold": threshold,
        "model": next((i.model for i in items if i.model), None),
        "generating": any(i.status == "pending" for i in items),
        "generated_at": max((i.explained_at for i in items if i.explained_at), default=None),
        "session": public_item(session) if session else None,
        "summary": review_summary(items, threshold),
        "items": {i.item_id: public_item(i) for i in items},
    }
