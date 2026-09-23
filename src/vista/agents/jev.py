"""The single place agents ask Jev (TypeSafe System One) for a typed judgment.

`llm.chat` returns prose that `parse` must interpret. `judge` returns answers that are
already typed — a probability, a label with its distribution, a position on an ordered
rubric — so code supplies the facts and the candidates and the model only selects and
grades among them. Nothing it returns can name an app, a record or a system that code
did not put in front of it.

Same three modes as llm.py, chosen without code changes:
  stub    — no VISTA_TYPESAFE_API_KEY: conservative answers (no / none / 0), fixed token counts
  cassette— VISTA_JEV_CASSETTE=<file>: replay recorded answers by request hash, record misses when live
  live    — key set: POST {typesafe_base_url}/systemone
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from vista.config import settings

STUB_MODEL = "stub-jev-v0"
NONE = "none"  # the escape hatch every selection should carry

# ---- questions ------------------------------------------------------------------


def noul(instructions: Any, criteria: dict | None = None) -> dict:
    """Yes/no; the answer is the probability of yes."""
    q: dict = {"type": "noul", "instructions": instructions}
    if criteria:
        q["criteria"] = criteria
    return q


def choice(instructions: Any, criteria: dict[str, Any]) -> dict:
    """One label from `criteria` (label → description or None), with the full distribution."""
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions: Any, criteria: list[Any]) -> dict:
    """Expected position on an ordered rubric; `criteria[i]` describes level i."""
    return {"type": "score", "instructions": instructions, "criteria": list(criteria)}


def pick(instructions: Any, candidates: list[str], none_text: str = "None of these.") -> dict:
    """A choice over code-found candidates that can always answer 'none'."""
    return choice(instructions, {**{c: None for c in candidates}, NONE: none_text})


# ---- request / result -------------------------------------------------------------


@dataclass(frozen=True)
class JudgeRequest:
    state: Any
    questions: dict[str, dict]

    def key(self, model: str) -> str:
        h = hashlib.sha256()
        for part in (model, json.dumps(self.state, sort_keys=True, default=str), json.dumps(self.questions, sort_keys=True, default=str)):
            h.update(part.encode())
            h.update(b"\0")
        return h.hexdigest()


@dataclass(frozen=True)
class Judgment:
    model: str
    answers: dict[str, dict] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    source: str = "live"  # live|stub|cassette

    def noul(self, q: str) -> float:
        return float(self.answers[q]["noul"])

    def choice(self, q: str) -> tuple[str, float]:
        """(label, probability of that label)."""
        a = self.answers[q]
        return a["choice"], float(a["probabilities"].get(a["choice"], 0.0))

    def probabilities(self, q: str) -> dict[str, float]:
        return {k: float(v) for k, v in self.answers[q]["probabilities"].items()}

    def score(self, q: str) -> tuple[float, float]:
        """(expected score, confidence)."""
        a = self.answers[q]
        return float(a["score"]), float(a.get("confidence", 0.0))


class Cassette:
    """JSON file of {request_key: Judgment}. Replays hits; records misses when a key is configured."""

    def __init__(self, path: Path):
        self.path = path
        self.entries: dict[str, dict] = json.loads(path.read_text()) if path.exists() else {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Judgment | None:
        entry = self.entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        self.hits += 1
        return Judgment(**{**entry, "source": "cassette"})

    def put(self, key: str, result: Judgment) -> None:
        self.entries[key] = {k: v for k, v in asdict(result).items() if k != "source"}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries, indent=1, sort_keys=True))


def _cassette() -> Cassette | None:
    path = os.environ.get("VISTA_JEV_CASSETTE")
    return Cassette(Path(path)) if path else None


# ---- backends ----------------------------------------------------------------------


def stub_answers(questions: dict[str, dict]) -> dict[str, dict]:
    """The answer a cautious reviewer gives without looking: no, none, lowest level."""
    out: dict[str, dict] = {}
    for name, q in questions.items():
        if q["type"] == "noul":
            out[name] = {"type": "noul", "noul": 0.0}
        elif q["type"] == "choice":
            labels = list(q["criteria"])
            chosen = NONE if NONE in q["criteria"] else labels[0]
            out[name] = {"type": "choice", "choice": chosen, "confidence": 0.0, "probabilities": {k: 1.0 / len(labels) for k in labels}}
        else:
            levels = q["criteria"]
            out[name] = {
                "type": "score",
                "score": 0.0,
                "confidence": 0.0,
                "legend": {str(i): lvl for i, lvl in enumerate(levels)},
                "probabilities": {str(i): (1.0 if i == 0 else 0.0) for i in range(len(levels))},
            }
    return out


RETRY_STATUSES = {408, 429, 500, 502, 503, 504, 529}


def live_judge(request: JudgeRequest, model: str | None = None) -> Judgment:
    model = model or settings.typesafe_model
    body = {"model": model, "state": request.state, "questions": request.questions}
    headers = {"Authorization": f"Bearer {settings.typesafe_api_key}", "Content-Type": "application/json"}
    url = settings.typesafe_base_url.rstrip("/") + "/systemone"
    delay = 0.5
    for attempt in range(4):
        resp = httpx.post(url, json=body, headers=headers, timeout=60.0)
        if resp.status_code in RETRY_STATUSES and attempt < 3:
            time.sleep(delay)
            delay *= 2
            continue
        if resp.status_code != 200:
            raise RuntimeError(f"Jev {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
        missing = set(request.questions) - set(data.get("answers", {}))
        if missing:
            raise RuntimeError(f"Jev answered without {sorted(missing)}")
        usage = data.get("usage") or {}
        return Judgment(
            model=data.get("model", model),
            answers=data["answers"],
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
        )
    raise AssertionError("unreachable")


def judge(state: Any, questions: dict[str, dict], model: str | None = None, cassette: Cassette | None = None) -> Judgment:
    if not questions:
        return Judgment(model=STUB_MODEL, source="stub")
    model = model or settings.typesafe_model
    request = JudgeRequest(state=state, questions=questions)
    cassette = cassette or _cassette()
    if cassette is not None:
        hit = cassette.get(request.key(model))
        if hit is not None:
            return hit
    if not settings.typesafe_api_key:
        return Judgment(model=STUB_MODEL, answers=stub_answers(questions), input_tokens=600, output_tokens=0, source="stub")
    result = live_judge(request, model)
    if cassette is not None:
        cassette.put(request.key(model), result)
    return result
