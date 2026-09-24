"""The planner: one typed judgment per step, and a separate one to verify the result.

Pure. No database, no clock of its own, no harness: `plan_step` is given the state, the
approved definition, the bound inputs, the facts gathered so far and the current observation,
and asks Jev four questions — which primitive next, which candidate, which input value,
how likely this commits something irreversible — over labels code built. Everything that
decides whether the answer is *acted on* is a constant here, so a policy change never re-runs
inference. `verify` asks about the goal and each success criterion against the final
observation only, with no view of the plan, so it cannot rationalise the run's own story.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from vista.agents.jev import NONE, Judgment, choice, judge, noul, pick
from vista.computer_use.harness import (
    ALWAYS_GATED,
    CONTROL_PRIMITIVES,
    PRIMITIVES,
    TARGETED,
    VALUED,
    WRITE_PRIMITIVES,
    Action,
    Candidate,
    Observation,
)

ACTION_CONFIDENCE = 0.5  # p(chosen primitive) needed to act at all
TARGET_CONFIDENCE = 0.6  # p(chosen candidate) needed to touch it
VALUE_CONFIDENCE = 0.6  # p(chosen input) needed to type it
DEFAULT_RISK_THRESHOLD = 0.3  # p(irreversible) at which the run pauses for a person
MAX_CONSECUTIVE_NONE = 2  # "nothing fits" this many times in a row → pause
CRITERION_THRESHOLD = 0.7
GOAL_THRESHOLD = 0.7
HISTORY_WINDOW = 8
MAX_VALUE_CANDIDATES = 60

JudgeFn = Callable[..., Judgment]


# ---- state that survives between jobs -------------------------------------------------------


@dataclass
class PlannerState:
    n: int = 0  # steps issued so far; the next step is n + 1
    history: list[dict] = field(default_factory=list)
    facts: dict = field(default_factory=dict)  # facts gathered by read/extract/http_get, keyed by step id
    decisions: dict[str, dict] = field(default_factory=dict)  # step_id → {"decision": approve|deny, ...}
    undo: list[dict] = field(default_factory=list)
    consecutive_none: int = 0
    observation: dict | None = None  # last observation, JSON
    pending: dict | None = None  # an action handed to the recorder and not yet answered: {"action", "gated"}
    node: str | None = None  # graph planner: the state key the run is believed to be in
    trajectory: list[dict] = field(default_factory=list)  # graph planner: edges acted on, {"edge", "step_id", "seq", "effect_seen"}
    proposals: list[dict] = field(default_factory=list)  # graph planner: edges that paused for a person, {"edge", "step_id"}

    @classmethod
    def from_checkpoint(cls, data: dict | None) -> PlannerState:
        data = data or {}
        return cls(
            n=int(data.get("n", 0)),
            history=list(data.get("history", [])),
            facts=dict(data.get("facts", {})),
            decisions=dict(data.get("decisions", {})),
            undo=list(data.get("undo", [])),
            consecutive_none=int(data.get("consecutive_none", 0)),
            observation=data.get("observation"),
            pending=data.get("pending"),
            node=data.get("node"),
            trajectory=list(data.get("trajectory", [])),
            proposals=list(data.get("proposals", [])),
        )

    def to_checkpoint(self) -> dict:
        return {
            "n": self.n,
            "history": self.history,
            "facts": self.facts,
            "decisions": self.decisions,
            "undo": self.undo,
            "consecutive_none": self.consecutive_none,
            "observation": self.observation,
            "pending": self.pending,
            "node": self.node,
            "trajectory": self.trajectory,
            "proposals": self.proposals,
        }

    def executed(self, step_id: str) -> bool:
        return any(h.get("step_id") == step_id and h.get("executed") for h in self.history)


# ---- decisions --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Act:
    action: Action
    gated: bool = False  # a write the owner already approved for this step


@dataclass(frozen=True)
class Pause:
    reason: str  # irreversible|no_action|ambiguous_target|missing_value|dry_run
    step_id: str
    request: dict  # what the workspace shows and the owner decides on


@dataclass(frozen=True)
class Finish:
    reason: str  # done|dry_run_stopped_before_write


@dataclass(frozen=True)
class Stop:
    reason: str  # denied


Decision = Act | Pause | Finish | Stop


# ---- value candidates: only declared inputs and gathered facts --------------------------------


def value_candidates(inputs: dict[str, dict], facts: dict) -> dict[str, tuple[str, str]]:
    """label → (source name, literal value). Sources are `value` inputs and the fields of
    rows gathered by earlier read/extract steps; the model never sees anything else as typeable."""
    out: dict[str, tuple[str, str]] = {}
    for name, binding in inputs.items():
        if binding.get("kind") == "value" and binding.get("value") not in (None, ""):
            out[f"input: {name}"] = (name, str(binding["value"]))
    for step_id, gathered in facts.items():
        rows = gathered.get("rows") if isinstance(gathered, dict) else None
        if not rows:
            continue
        for i, row in enumerate(rows[:20]):
            if not isinstance(row, dict):
                continue
            for col, val in row.items():
                if val in (None, ""):
                    continue
                label = f"{step_id} row {i + 1} · {col}"
                out[label] = (f"{step_id}:row{i + 1}:{col}", str(val)[:512])
                if len(out) >= MAX_VALUE_CANDIDATES:
                    return out
    return out


def _preview(name: str, binding: dict) -> dict:
    kind = binding.get("kind", "value")
    if kind == "value":
        return {"name": name, "kind": kind, "preview": str(binding.get("value", ""))[:80]}
    if kind == "records":
        return {"name": name, "kind": kind, "preview": f"{binding.get('table')} × {len(binding.get('ids', []))}"}
    return {"name": name, "kind": kind, "preview": binding.get("filename") or binding.get("artifact_id", "")}


# ---- the step judgment ------------------------------------------------------------------------


# Control state flags the harness computed in code (booleans only — never a value).
CANDIDATE_FLAGS = ("has_value", "disabled", "checked")


def _candidate_view(c: Candidate) -> dict:
    view = {"id": c.id, "label": c.label, "kind": c.kind}
    flags = {k: bool(c.attrs[k]) for k in CANDIDATE_FLAGS if k in c.attrs}
    return {**view, **flags} if flags else view


def step_state(
    definition: dict, inputs: dict[str, dict], state: PlannerState, observation: Observation, primitives: set[str], limits_left: dict
) -> dict:
    return {
        "goal": definition["goal"],
        "success_criteria": definition["success_criteria"],
        "inputs": [_preview(n, b) for n, b in inputs.items()],
        "allowed_tools": definition["allowed_tools"],
        "primitives": {p: PRIMITIVES[p] for p in sorted(primitives) if p in PRIMITIVES},
        "history": state.history[-HISTORY_WINDOW:],
        "observation": {
            "harness": observation.harness,
            "facts": observation.facts,
            "candidates": [_candidate_view(c) for c in observation.candidates],
        },
        "facts_gathered": {k: v for k, v in list(state.facts.items())[-4:]},
        "limits_left": limits_left,
    }


def step_questions(primitives: set[str], observation: Observation, values: dict[str, tuple[str, str]]) -> dict[str, dict]:
    offered = {p: PRIMITIVES[p] for p in sorted(primitives - CONTROL_PRIMITIVES) if p in PRIMITIVES}
    offered["done"] = PRIMITIVES["done"]
    offered["ask_human"] = PRIMITIVES["ask_human"]
    offered[NONE] = "Nothing here is the right next step, or the situation is unclear."
    questions = {
        "next_action": choice(
            "Given `goal`, `success_criteria`, the facts gathered so far, `history` and the current `observation`, "
            "which primitive in `primitives` is the right next step? "
            "Choose `done` when the goal is already met by what was done, `ask_human` when the safe next step is not on screen.",
            offered,
        ),
        "irreversible": noul(
            "Would the right next step, carried out on the current `observation`, submit, send, delete, pay, sign, "
            "or otherwise commit something a person could not simply undo?",
            {
                "true": "Yes: it commits or destroys something outside this run.",
                "false": "No: it only opens, reads, selects, types into a field, or waits.",
            },
        ),
    }
    if observation.candidates:
        questions["target"] = pick(
            "Which candidate in `observation.candidates` should the next step act on? "
            "Match by meaning — a “Save” button may be labelled “Post”; a supplier field may be called “Vendor”.",
            [c.label for c in observation.candidates],
            "None of these is the right target.",
        )
    if values:
        questions["value"] = pick(
            "If the next step types something, which of these declared inputs or gathered values is the one to type?",
            list(values.keys()),
            "Nothing declared fits; do not type anything.",
        )
    return questions


STEP_NAMESPACE = uuid.UUID("6f1c2a7e-3b0d-4c7a-9a2e-0c0a7c5e0001")


def step_id_for(run_id: str, seq: int) -> str:
    """Deterministic per (run, seq): a retried job files the same step, never a second one."""
    return str(uuid.uuid5(STEP_NAMESPACE, f"{run_id}:{seq}"))


def plan_step(
    state: PlannerState,
    definition: dict,
    inputs: dict[str, dict],
    observation: Observation,
    *,
    run_id: str,
    primitives: set[str],
    limits_left: dict,
    mode: str = "sandbox",
    risk_threshold: float = DEFAULT_RISK_THRESHOLD,
    judge_fn: JudgeFn = judge,
) -> tuple[Decision, Judgment, dict]:
    """One step: ask, then apply the gates in code. Returns the decision, the raw judgment
    (for the ledger) and the `model_call` event detail."""
    seq = state.n + 1
    step_id = step_id_for(run_id, seq)
    values = value_candidates(inputs, state.facts)
    label_to_candidate = {c.label: c for c in observation.candidates}
    questions = step_questions(primitives, observation, values)
    judgment = judge_fn(step_state(definition, inputs, state, observation, primitives, limits_left), questions)

    primitive, p_action = judgment.choice("next_action")
    p_irreversible = judgment.noul("irreversible")
    target = p_target = None
    if "target" in questions:
        label, p_target = judgment.choice("target")
        target = label_to_candidate.get(label) if label != NONE else None
    value_input = value = None
    p_value = None
    if "value" in questions:
        label, p_value = judgment.choice("value")
        if label != NONE and label in values:
            value_input, value = values[label]

    detail = {
        "step_id": step_id,
        "seq": seq,
        "phase": "plan",
        "next_action": primitive,
        "p_action": round(p_action, 3),
        "target": target.label if target else None,
        "p_target": round(p_target, 3) if p_target is not None else None,
        "value_input": value_input,
        "p_value": round(p_value, 3) if p_value is not None else None,
        "p_irreversible": round(p_irreversible, 3),
    }
    candidates_out = [
        {
            "id": c.id,
            "label": c.label,
            "role": c.role,
            "p": round(judgment.probabilities("target").get(c.label, 0.0), 3) if "target" in questions else None,
        }
        for c in observation.candidates[:12]
    ]

    def pause(reason: str, description: str) -> Pause:
        return Pause(
            reason,
            step_id,
            {
                "step_id": step_id,
                "seq": seq,
                "harness": observation.harness,
                "action": primitive,
                "description": description,
                "target": {"label": target.label, "role": target.role, "id": target.id} if target else None,
                "risk": {"irreversible": round(p_irreversible, 3)},
                "candidates": candidates_out,
                "chosen": target.id if target else None,
                "value_from": value_input,
                "reason": reason,
            },
        )

    # --- gates, in order ---
    if primitive == NONE or primitive not in primitives or p_action < ACTION_CONFIDENCE:
        state.consecutive_none += 1
        if state.consecutive_none >= MAX_CONSECUTIVE_NONE:
            return pause("no_action", "No safe next step could be chosen from what is on screen."), judgment, detail
        return Act(Action(step_id, seq, "wait", args={"ms": 1500})), judgment, detail
    state.consecutive_none = 0
    if primitive == "done":
        return Finish("done"), judgment, detail
    if primitive == "ask_human":
        return pause("ask_human", "The agent asked for a person before continuing."), judgment, detail
    if primitive in TARGETED and (target is None or (p_target or 0) < TARGET_CONFIDENCE):
        return pause("ambiguous_target", f"No candidate on screen is clearly the target for “{primitive}”."), judgment, detail
    if primitive in VALUED and (value is None or (p_value or 0) < VALUE_CONFIDENCE):
        return pause("missing_value", "No declared input or gathered value clearly belongs in that field."), judgment, detail
    args: dict = {}
    if primitive == "navigate":
        url = value if value and value.startswith(("http://", "https://")) and (p_value or 0) >= VALUE_CONFIDENCE else None
        if url is None:
            return pause("missing_value", "No declared input names the page to open."), judgment, detail
        args = {"url": url}
    elif primitive == "press":
        args = {"key": "Enter"}
    elif primitive == "wait":
        args = {"ms": 1500}

    action = Action(
        step_id,
        seq,
        primitive,
        target=target if primitive in TARGETED else None,
        value_input=value_input if primitive in VALUED or primitive == "navigate" else None,
        value=value if primitive in VALUED else None,
        args=args,
    )
    if mode == "dry_run" and primitive in WRITE_PRIMITIVES:
        return Finish("dry_run_stopped_before_write"), judgment, detail
    decided = state.decisions.get(step_id, {}).get("decision")
    if decided == "deny":
        return Stop("denied"), judgment, detail
    if (primitive in ALWAYS_GATED or p_irreversible >= risk_threshold) and decided != "approve":
        return pause("irreversible", action.describe()), judgment, detail
    return Act(action, gated=decided == "approve"), judgment, detail


# ---- verification ---------------------------------------------------------------------------------


@dataclass
class Verification:
    goal_met: bool
    p_goal: float
    criteria: list[dict]
    judgment: Judgment

    @property
    def passed(self) -> bool:
        return self.goal_met and all(c["met"] for c in self.criteria)

    def to_json(self) -> dict:
        return {"goal_met": self.goal_met, "p_goal": self.p_goal, "criteria": self.criteria, "passed": self.passed}


def verify(definition: dict, observation: Observation | None, facts: dict, *, judge_fn: JudgeFn = judge) -> Verification:
    """Independent read-back: the state carries the goal, the criteria, the final observation and
    the gathered facts — never the plan or its history."""
    state: dict[str, Any] = {
        "goal": definition["goal"],
        "success_criteria": definition["success_criteria"],
        "final_observation": {
            "harness": observation.harness,
            "facts": observation.facts,
            "visible": [c.label for c in observation.candidates],
        }
        if observation
        else None,
        "facts_gathered": facts,
    }
    questions = {
        "goal_met": noul(
            "Judging only from `final_observation` and `facts_gathered`, has `goal` actually been achieved?",
            {
                "true": "The evidence shows the goal's outcome exists now.",
                "false": "The evidence does not show it, or shows something else.",
            },
        )
    }
    for i, criterion in enumerate(definition["success_criteria"]):
        questions[f"criterion_{i}"] = noul(
            f"Judging only from `final_observation` and `facts_gathered`, is success criterion {i + 1} satisfied: “{criterion}”?",
            {"true": "The evidence shows it holds.", "false": "The evidence contradicts it or does not show it."},
        )
    judgment = judge_fn(state, questions)
    p_goal = judgment.noul("goal_met")
    criteria = []
    for i, criterion in enumerate(definition["success_criteria"]):
        p = judgment.noul(f"criterion_{i}")
        criteria.append({"text": criterion, "p": round(p, 3), "met": p >= CRITERION_THRESHOLD})
    return Verification(goal_met=p_goal >= GOAL_THRESHOLD, p_goal=round(p_goal, 3), criteria=criteria, judgment=judgment)


def facts_from_result(result_facts: dict) -> dict:
    """Keep only what later steps may type from or verification may cite."""
    keep = {}
    for k, v in result_facts.items():
        if k in ("rows", "columns", "text", "summary", "url", "title", "status", "previous_value", "count"):
            keep[k] = v
    return keep


def candidates_from_labels(labels: list[str]) -> list[Candidate]:
    return [Candidate(id=label, role="value", name=label, kind="value") for label in labels]
