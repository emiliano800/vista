"""The harness interface: how the agent observes and acts on a computer.

A harness turns "what is there" into *candidates* that code enumerated, and "do this"
into one bounded effect. Local harnesses (documents, http, workspace) run in the worker.
Remote harnesses (browser, desktop) run inside the employee's recorder: the worker files a
step request and suspends; the recorder pulls it, performs it, and posts the result back.
Either way the planner sees the same `Observation`/`ActionResult` shapes, and nothing a
model returns can name a target or a value that code did not put in front of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol

Kind = Literal["documents", "http", "workspace", "browser", "desktop"]
Primitive = Literal[
    "navigate",
    "click",
    "type_value",
    "press",
    "read",
    "extract",
    "http_get",
    "submit",
    "create_task",
    "screenshot",
    "wait",
    "done",
    "ask_human",
    "none",
]

LOCAL_KINDS: frozenset[str] = frozenset({"documents", "http", "workspace"})
REMOTE_KINDS: frozenset[str] = frozenset({"browser", "desktop"})
# Primitives that change something outside the run. `submit` commits; it always pauses for a human.
WRITE_PRIMITIVES: frozenset[str] = frozenset({"type_value", "click", "submit", "create_task"})
ALWAYS_GATED: frozenset[str] = frozenset({"submit"})
# Planner-only outcomes: they never reach a harness.
CONTROL_PRIMITIVES: frozenset[str] = frozenset({"done", "ask_human", "none"})
# Primitives that need a target from the observation / a value from the inputs.
TARGETED: frozenset[str] = frozenset({"click", "type_value", "submit", "extract", "read"})
VALUED: frozenset[str] = frozenset({"type_value"})
MAX_CANDIDATES = 40

PRIMITIVES: dict[str, str] = {
    "navigate": "Open a page or a document that the run's inputs name; nothing is entered or changed.",
    "click": "Press one named control on the screen (a button, link, tab, menu item, checkbox) that does not itself commit anything.",
    "type_value": "Put one value from the run's declared inputs into one named field.",
    "press": "Press a named key such as Enter, Tab or Escape.",
    "read": "Read one named thing (a document, a record, a page region) so its contents become facts.",
    "extract": "Pull structured rows or fields out of one named table or record into facts.",
    "http_get": "Fetch one allow-listed endpoint of a connected system; the response becomes facts.",
    "submit": "Press the control that commits the entered values (Submit, Save, Send, Post). Always waits for a person first.",
    "create_task": "Create a task in the Vista workspace for a person, citing the facts gathered.",
    "screenshot": "Capture the screen as evidence only; nothing is read from it.",
    "wait": "Wait briefly for the screen to settle.",
    "done": "The goal is met, or every remaining step needs a person: stop and verify.",
    "ask_human": "Nothing on screen matches the next step safely; pause for a person.",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Candidate:
    """One thing the harness found that a step could target. `id` is the harness's own
    handle (a backendDOMNodeId, `row:12`, `doc:<artifact>`, `button:Save`); `label` is what
    the model sees."""

    id: str
    role: str
    name: str
    kind: str = "interactive"  # interactive|field|link|row|document|record|endpoint
    attrs: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.role}: {self.name}"

    def to_json(self) -> dict:
        return {"id": self.id, "role": self.role, "name": self.name, "kind": self.kind, "attrs": self.attrs}

    @classmethod
    def from_json(cls, data: dict) -> Candidate:
        return cls(
            id=str(data["id"]),
            role=str(data.get("role", "")),
            name=str(data.get("name", ""))[:200],
            kind=str(data.get("kind", "interactive")),
            attrs=dict(data.get("attrs") or {}),
        )


def rank_candidates(candidates: list[Candidate], limit: int = MAX_CANDIDATES) -> list[Candidate]:
    """Deterministic, code-owned order: the things a step can act on first, then the
    shorter names. Capped so the model always sees a bounded list."""
    order = {"field": 0, "interactive": 1, "link": 2, "row": 3, "record": 3, "document": 4, "endpoint": 4}
    seen: set[tuple[str, str]] = set()
    unique = []
    for c in candidates:
        key = (c.role, c.name.casefold())
        if key in seen or not c.name:
            continue
        seen.add(key)
        unique.append(c)
    unique.sort(key=lambda c: (order.get(c.kind, 5), len(c.name), c.name))
    return unique[:limit]


@dataclass
class Observation:
    harness: str
    facts: dict
    candidates: list[Candidate]
    observation_id: str | None = None
    artifact_key: str | None = None
    captured_at: str = field(default_factory=_now)

    def to_json(self) -> dict:
        return {
            "harness": self.harness,
            "facts": self.facts,
            "candidates": [c.to_json() for c in self.candidates],
            "observation_id": self.observation_id,
            "artifact_key": self.artifact_key,
            "captured_at": self.captured_at,
        }

    @classmethod
    def from_json(cls, data: dict) -> Observation:
        return cls(
            harness=data["harness"],
            facts=dict(data.get("facts") or {}),
            candidates=rank_candidates([Candidate.from_json(c) for c in data.get("candidates") or []]),
            observation_id=data.get("observation_id"),
            artifact_key=data.get("artifact_key"),
            captured_at=data.get("captured_at") or _now(),
        )

    def find(self, candidate_id: str) -> Candidate | None:
        return next((c for c in self.candidates if c.id == candidate_id), None)


@dataclass(frozen=True)
class Action:
    step_id: str
    seq: int
    primitive: str
    target: Candidate | None = None
    value_input: str | None = None  # the *name* of the input a value came from (what the ledger records)
    value: str | None = None  # the literal value (only ever copied from inputs or gathered facts)
    args: dict = field(default_factory=dict)

    def describe(self) -> str:
        what = PRIMITIVES.get(self.primitive, self.primitive).split(".")[0]
        if self.primitive == "type_value" and self.target:
            return f"Type the value of “{self.value_input}” into {self.target.label}"
        if self.primitive in ("click", "submit") and self.target:
            return f"{'Submit with' if self.primitive == 'submit' else 'Click'} {self.target.label}"
        if self.primitive in ("read", "extract") and self.target:
            return f"{self.primitive.capitalize()} {self.target.label}"
        if self.primitive == "navigate":
            return f"Open {self.args.get('url') or self.args.get('name') or 'the page'}"
        if self.primitive == "http_get":
            return f"Fetch {self.args.get('path', '')}"
        if self.primitive == "press":
            return f"Press {self.args.get('key', 'a key')}"
        return what

    def to_json(self) -> dict:
        return {
            "step_id": self.step_id,
            "seq": self.seq,
            "primitive": self.primitive,
            "target": self.target.to_json() if self.target else None,
            "value_input": self.value_input,
            "args": self.args,
        }


@dataclass
class ActionResult:
    step_id: str
    ok: bool
    description: str = ""
    facts: dict = field(default_factory=dict)
    undo: dict | None = None
    artifact_key: str | None = None
    error: str | None = None
    observation: Observation | None = None  # the world after the action, when the harness saw it

    def to_json(self) -> dict:
        return {
            "step_id": self.step_id,
            "ok": self.ok,
            "description": self.description,
            "facts": self.facts,
            "undo": self.undo,
            "artifact_key": self.artifact_key,
            "error": self.error,
            "observation": self.observation.to_json() if self.observation else None,
        }


@dataclass(frozen=True)
class ObserveContext:
    step_id: str
    seq: int
    goal: str
    inputs: dict[str, dict]
    last_action: Action | None = None


class HarnessSuspended(Exception):
    """A remote harness filed a step for the recorder; the run must wait for the result."""

    def __init__(self, step_id: str, expires_at: datetime, request: dict):
        super().__init__(f"step {step_id} waits for the recorder")
        self.step_id = step_id
        self.expires_at = expires_at
        self.request = request


class Harness(Protocol):
    kind: str

    def capabilities(self) -> set[str]: ...

    def observe(self, context: ObserveContext) -> Observation: ...

    def act(self, action: Action) -> ActionResult: ...

    def close(self) -> None: ...


class HarnessUnavailable(RuntimeError):
    """The definition needs a harness kind nothing can provide right now."""
