"""Typed verification of a v3 run: predicates over slots on the goal frame.

`present` and `read_back` are decided in code; `graded` is the one place Jev judges, over the
criterion's slot and at the threshold the task declares. Read-back comparison happens on the
device (the sidecar compares the expected value with what it reads and answers a boolean);
the ledger and the run outcome carry slot names and booleans only — never values.

A run passes when the goal frame was reached and every criterion holds. A `read_back` that
was never answered — no `read_back_via` edge, nothing expected to compare, the device never
answered — is *not met*; a write without read-back can never be called verified.
"""

from __future__ import annotations

from typing import Any

from vista.agents.jev import noul
from vista.computer_use.graph import Graph
from vista.computer_use.harness import Observation
from vista.computer_use.planner import CRITERION_THRESHOLD, JudgeFn, PlannerState, Verification
from vista.computer_use.run_v3 import _empty_judgment, criteria_of, observed_l0

DEFAULT_GRADED_THRESHOLD = CRITERION_THRESHOLD


def slot_present(slot: str, l0: frozenset[str], facts: dict) -> bool:
    if f"have:{slot}" in l0 or f"read:{slot}" in l0 or f"open:{slot}" in l0:
        return True
    return any(slot in (v if isinstance(v, dict) else {}) for v in facts.values())


def read_back_answers(state: PlannerState) -> dict[str, bool]:
    return {str(k): bool(v) for k, v in (state.recovery.get("read_back") or {}).items()}


def verify_v3(
    definition: dict,
    observation: Observation | None,
    state: PlannerState,
    *,
    judge_fn: JudgeFn,
) -> Verification:
    graph = Graph.from_definition(definition)
    goal = (definition.get("graph") or {}).get("goal") or {}
    l0 = observed_l0(observation) if observation is not None else frozenset()
    goal_l0 = frozenset(goal.get("l0") or [])
    on_goal = bool(goal_l0) and goal_l0 == l0
    if not l0 and graph is not None and goal.get("node"):
        on_goal = state.node == goal["node"] and bool(state.trajectory)
    answers = read_back_answers(state)
    criteria: list[dict] = []
    graded: list[dict] = []
    for c in criteria_of(definition):
        slot = str(c["slot"])
        kind = c["type"]
        if kind == "present":
            criteria.append({"type": kind, "slot": slot, "met": slot_present(slot, l0, state.facts), "by": "code"})
        elif kind == "read_back":
            met = answers.get(slot)
            criteria.append({"type": kind, "slot": slot, "met": bool(met), "by": "device" if met is not None else "unanswered"})
        else:
            graded.append(c)
    judgment = _empty_judgment()
    if graded:
        view: dict[str, Any] = {
            "goal": definition.get("goal"),
            "goal_frame_reached": on_goal,
            "slots_present": sorted(t.split(":", 1)[1] for t in l0 if t.startswith(("have:", "read:"))),
            "final_observation": {"harness": observation.harness, "l0": sorted(l0), "l1": observation.facts.get("l1")}
            if observation is not None
            else None,
        }
        questions = {
            f"graded_{i}": noul(
                f"Judging only from `final_observation` and `slots_present`, does slot “{c['slot']}” hold the outcome the task needs?",
                {"true": "The evidence shows it does.", "false": "The evidence does not show it, or shows something else."},
            )
            for i, c in enumerate(graded)
        }
        judgment = judge_fn(view, questions)
        for i, c in enumerate(graded):
            p = judgment.noul(f"graded_{i}")
            threshold = float(c.get("threshold") or DEFAULT_GRADED_THRESHOLD)
            criteria.append({"type": "graded", "slot": str(c["slot"]), "met": p >= threshold, "p": round(p, 3), "by": "jev"})
    return Verification(goal_met=on_goal, p_goal=1.0 if on_goal else 0.0, criteria=criteria, judgment=judgment)
