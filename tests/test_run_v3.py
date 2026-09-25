"""Run loop v3: locate by L0 equality, targets in code, Jev only on ambiguity, straight line only
where tier and class allow, recovery before pause and never affirmative. No database, no network."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from vista.agents.jev import NONE, Judgment
from vista.automation.schemas import WorkflowDefinition
from vista.computer_use.graph import DONE, Graph, edge_label, node_label
from vista.computer_use.harness import Candidate, Observation
from vista.computer_use.harness_remote import observation_from_result
from vista.computer_use.planner import Act, Finish, Pause, PlannerState
from vista.computer_use.run_v3 import (
    RECOVERY_BUDGET,
    descriptor_score,
    dismiss_control,
    is_v3,
    locate,
    modal_is_dismissable,
    plan_v3_step,
    resolve_target,
    straight_line,
)

GRAPH = json.loads((Path(__file__).parent / "fixtures" / "plan_invoice_v3.json").read_text())
RUN = "00000000-0000-0000-0000-00000000c0de"
NODES = {n["key"]: n for n in GRAPH["nodes"]}
EDGES = {e["id"]: e for e in GRAPH["edges"]}
DEFINITION = {
    "goal": "Enter the invoice total into the accounting system",
    "required_inputs": ["input_1", "Sandbox form URL"],
    "allowed_tools": ["read_source_records", "map_fields", "write_destination_records", "compare_with_manual_entry"],
    "success_criteria": ["The bill total matches the invoice"],
    "environment": "sandbox",
    "limits": {"max_steps": 10, "max_runtime_seconds": 300, "max_cost_usd": "1.00"},
    "graph": GRAPH,
}
INPUTS = {
    "input_1": {"kind": "value", "value": "ACME"},
    "Sandbox form URL": {"kind": "value", "value": "http://localhost:8765/_sandbox/bill.html"},
}
PRIMS = {"navigate", "click", "type_value", "press", "read", "extract", "submit", "wait", "done", "ask_human", "none"}
LIMITS = {"steps": 10, "seconds": 300}

# Nodes of the fixture by what they hold.
BILL_EMPTY = next(n for n in GRAPH["nodes"] if sorted(n["l0"]) == ["in:d9689a98516bc009", "read:fact:f1"])  # → type amount
BILL_AMOUNT = next(n for n in GRAPH["nodes"] if sorted(n["l0"]) == ["have:field:amount", "in:d9689a98516bc009", "read:fact:f1"])
BILL_FULL = next(
    n for n in GRAPH["nodes"] if sorted(n["l0"]) == ["have:field:amount", "have:field:input_1", "in:d9689a98516bc009", "read:fact:f1"]
)
PDF_OPEN = next(n for n in GRAPH["nodes"] if sorted(n["l0"]) == ["in:fee208d94242f0d7", "open:doc:d1"])
PDF_READ = next(n for n in GRAPH["nodes"] if sorted(n["l0"]) == ["in:fee208d94242f0d7", "open:doc:d1", "read:fact:f1"])
TYPE_AMOUNT = next(e for e in GRAPH["edges"] if e["action_class"] == "type_value" and (e.get("descriptor") or {}).get("name") == "amount")
SUBMIT = next(e for e in GRAPH["edges"] if e["action_class"] == "submit")
NAVIGATE = next(e for e in GRAPH["edges"] if e["action_class"] == "navigate")


def field(id_: str, name: str, *, landmark: str = "form", disabled: bool = False) -> Candidate:
    return Candidate(
        id_, "textbox", name, "field", {"landmark": landmark, "position": "middle", "disabled": disabled, "harness": "browser"}
    )


def button(id_: str, name: str, *, primary: bool = False) -> Candidate:
    return Candidate(id_, "button", name, "interactive", {"landmark": "dialog", "primary": primary, "harness": "browser"})


def frame(l0: list[str], *candidates: Candidate, modal: bool = False, primary: dict | None = None, **facts) -> Observation:
    return Observation(
        "browser",
        {
            "l0": list(l0),
            "l1": {"landmarks": ["main", "form"], "modal": modal, "primary": primary, "controls": []},
            "sensitive": False,
            "settled": True,
            **facts,
        },
        list(candidates),
        observation_id="obs-1",
    )


class Judge:
    """Answers scripted per call; refuses to be called at all when `allowed` is 0."""

    def __init__(self, *specs: dict, allowed: int | None = None):
        self.specs = list(specs)
        self.allowed = allowed
        self.calls: list[tuple[dict, dict]] = []

    def __call__(self, state: dict, questions: dict) -> Judgment:
        self.calls.append((state, questions))
        assert self.allowed is None or len(self.calls) <= self.allowed, f"Jev was asked when code should have decided: {list(questions)}"
        spec = self.specs.pop(0) if self.specs else {}
        answers: dict = {}

        def choose(q: str, label: str, p: float) -> None:
            probs = {k: 0.0 for k in questions[q]["criteria"]}
            assert label in probs, f"{q}: {label!r} was never offered; offered {list(probs)}"
            probs[label] = p
            answers[q] = {"type": "choice", "choice": label, "confidence": p, "probabilities": probs}

        if "effect_seen" in questions:
            answers["effect_seen"] = {"type": "noul", "noul": spec.get("effect_seen", 0.9)}
        if "node" in questions:
            choose("node", spec.get("node", next(iter(questions["node"]["criteria"]))), spec.get("p_node", 0.9))
        if "edge" in questions:
            choose("edge", spec.get("edge", NONE), spec.get("p_edge", 0.9))
        if "irreversible" in questions:
            answers["irreversible"] = {"type": "noul", "noul": spec.get("irreversible", 0.05)}
        for q in questions:
            if q.startswith("target:"):
                wanted = spec.get("target", NONE)
                choose(q, wanted if wanted in questions[q]["criteria"] else NONE, spec.get("p_target", 0.9))
        if "value" in questions:
            choose("value", spec.get("value", NONE), spec.get("p_value", 0.9))
        return Judgment(model="jev-1.13.0", answers=answers, input_tokens=500)


def step(judge, observation, *, state=None, definition=DEFINITION, inputs=INPUTS, mode="sandbox", risk_threshold=0.3):
    state = state or PlannerState()
    decision, judgment, detail = plan_v3_step(
        state,
        definition,
        inputs,
        observation,
        run_id=RUN,
        primitives=PRIMS,
        limits_left=LIMITS,
        mode=mode,
        risk_threshold=risk_threshold,
        judge_fn=judge,
    )
    return decision, state, detail


def with_edge(changes: dict, edge_id: str) -> dict:
    d = copy.deepcopy(DEFINITION)
    e = next(e for e in d["graph"]["edges"] if e["id"] == edge_id)
    e.update(changes)
    WorkflowDefinition.model_validate(d)
    return d


# ---- locate ------------------------------------------------------------------------------------


def test_the_fixture_is_v3_and_locates_by_l0_equality_without_asking():
    obs = frame(BILL_EMPTY["l0"], field("c0", "amount"))
    assert is_v3(DEFINITION, obs)
    assert not is_v3(DEFINITION, Observation("browser", {"url": "x"}, []))
    graph = Graph.from_definition(DEFINITION)
    assert [n["key"] for n in locate(graph, frozenset(BILL_EMPTY["l0"]))] == [BILL_EMPTY["key"]]
    assert locate(graph, frozenset({"in:unknown"})) == []
    judge = Judge({"edge": edge_label(TYPE_AMOUNT)})
    decision, state, detail = step(judge, obs, inputs={**INPUTS, "fact:f1": {"kind": "value", "value": "1200"}})
    assert detail["located"] == 1 and detail["node"] == BILL_EMPTY["key"] and detail["p_node"] == 1.0
    assert detail["rejudged"] == 0
    ((_, questions),) = judge.calls
    assert "node" not in questions, "L0 located the run; Jev is not asked where it is"
    # Jev saw only the located node's own moves.
    offered = set(questions["edge"]["criteria"]) - {DONE, NONE}
    assert offered == {edge_label(e) for e in GRAPH["edges"] if e["frm"] == BILL_EMPTY["key"]}
    assert isinstance(decision, Pause) and decision.reason == "confirm"  # policy confirm on the type edge
    assert decision.request["target"]["id"] == "c0" and detail["target_by"] == "code"


def test_a_tie_on_l0_asks_jev_among_the_tied_nodes_only():
    d = copy.deepcopy(DEFINITION)
    twin = copy.deepcopy(BILL_EMPTY)
    twin["key"] = "ffffffffffffffff"
    twin["activity"] = "twin"
    d["graph"]["nodes"].append(twin)
    judge = Judge({"node": node_label(twin)}, {"edge": NONE})
    decision, state, detail = step(judge, frame(BILL_EMPTY["l0"], field("c0", "amount")), definition=d)
    first_questions = judge.calls[0][1]
    assert set(first_questions["node"]["criteria"]) == {node_label(twin), node_label(BILL_EMPTY), NONE}
    assert detail["located"] == 2 and detail["node"] == "ffffffffffffffff"


# ---- targets in code -----------------------------------------------------------------------------


def test_descriptor_scoring_needs_role_name_and_landmark_to_agree():
    d = {"role": "textbox", "name": "amount", "landmark": "form", "position": "middle", "aliases": ["total"]}
    assert descriptor_score(d, field("a", "amount")) == 1.0
    assert descriptor_score(d, field("a", "total")) == 0.9
    assert descriptor_score(d, field("a", "vendor")) == 0.0
    assert descriptor_score(d, field("a", "amount", landmark="banner")) == 0.0
    assert descriptor_score(d, field("a", "amount", disabled=True)) == 0.0
    assert descriptor_score(d, button("a", "amount")) == 0.0
    assert descriptor_score({"role": "textbox", "name": "{text}", "landmark": "form"}, field("a", "anything")) == 0.6


def test_exactly_one_match_resolves_in_code_several_go_to_jev_none_reobserves_then_pauses():
    one, matches = resolve_target(TYPE_AMOUNT, [field("c0", "amount"), field("c1", "vendor")])
    assert one is not None and one.id == "c0" and [c.id for c in matches] == ["c0"]
    two, matches = resolve_target(TYPE_AMOUNT, [field("c0", "amount"), field("c1", "amount")])
    assert two is None and [c.id for c in matches] == ["c0", "c1"]

    judge = Judge({"edge": edge_label(TYPE_AMOUNT), "target": "textbox: amount"})
    decision, state, detail = step(judge, frame(BILL_EMPTY["l0"], field("c0", "amount"), field("c1", "amount")))
    q = judge.calls[0][1]
    target_qs = [k for k in q if k.startswith("target:")]
    assert target_qs == [f"target:{TYPE_AMOUNT['id']}"], "only the ambiguous edge gets a target question"
    assert set(q[target_qs[0]]["criteria"]) == {"textbox: amount", NONE}  # both share the label; Jev cannot tell them apart
    assert detail["target_by"] == "jev"

    judge = Judge({"edge": edge_label(TYPE_AMOUNT)}, {"edge": edge_label(TYPE_AMOUNT)})
    state = PlannerState()
    decision, state, detail = step(judge, frame(BILL_EMPTY["l0"], field("c0", "vendor")), state=state)
    assert isinstance(decision, Act) and decision.action.primitive == "wait", "no target: re-observe once"
    state.n = 1
    decision, state, detail = step(judge, frame(BILL_EMPTY["l0"], field("c0", "vendor")), state=state)
    assert isinstance(decision, Pause) and decision.reason == "ambiguous_target"


# ---- straight line -------------------------------------------------------------------------------


def test_straight_line_only_for_unattended_navigational_or_linked_mutating_never_committing():
    graph = Graph.from_definition(DEFINITION)
    nav = {**NAVIGATE, "tier": "unattended"}
    assert straight_line(nav, INPUTS, graph)
    assert not straight_line({**NAVIGATE, "tier": "confirm"}, INPUTS, graph)
    assert not straight_line({**NAVIGATE, "tier": "unattended", "policy": "confirm"}, INPUTS, graph)
    typed = {**TYPE_AMOUNT, "tier": "unattended", "policy": "auto"}
    assert straight_line(typed, INPUTS, graph), "fact:f1 is transfer-linked (an edge produces it)"
    assert not straight_line({**typed, "slot": "field:loose"}, INPUTS, graph), "an unlinked slot is judged"
    assert not straight_line({**SUBMIT, "tier": "unattended", "policy": "auto"}, INPUTS, graph)
    with pytest.raises(ValueError):
        with_edge({"tier": "unattended"}, SUBMIT["id"])


def test_an_unattended_navigational_edge_acts_without_a_single_question():
    d = with_edge({"tier": "unattended"}, NAVIGATE["id"])
    judge = Judge(allowed=0)
    decision, state, detail = step(judge, frame(PDF_READ["l0"]), definition=d)
    assert isinstance(decision, Act) and decision.action.primitive == "navigate"
    assert detail["straight_line"] is True and judge.calls == []
    assert state.node == NAVIGATE["to"] and state.trajectory[-1]["edge"] == NAVIGATE["id"]


def test_the_same_edge_in_tier_ask_is_judged_and_paused():
    d = with_edge({"tier": "ask"}, NAVIGATE["id"])
    judge = Judge({"edge": edge_label(NAVIGATE)})
    decision, state, detail = step(judge, frame(PDF_READ["l0"]), definition=d)
    assert len(judge.calls) == 1 and detail["straight_line"] is False
    assert isinstance(decision, Pause) and decision.reason == "confirm"


# ---- pauses ----------------------------------------------------------------------------------------


def test_committing_edges_pause_even_when_jev_is_sure_it_is_harmless():
    judge = Judge({"edge": edge_label(SUBMIT), "irreversible": 0.0})
    decision, state, detail = step(judge, frame(BILL_FULL["l0"], button("c0", "save", primary=True)))
    assert isinstance(decision, Pause) and decision.reason == "irreversible"
    assert detail["irreversibility"] == "committing" and state.proposals == [{"edge": SUBMIT["id"], "step_id": decision.request["step_id"]}]


def test_p_irreversible_raises_the_class_but_never_lowers_it():
    d = with_edge({"tier": "confirm"}, NAVIGATE["id"])
    judge = Judge({"edge": edge_label(NAVIGATE), "irreversible": 0.45})
    decision, state, detail = step(judge, frame(PDF_READ["l0"]), definition=d)
    assert isinstance(decision, Pause) and decision.reason == "irreversible"
    assert detail["irreversibility"] == "committing"
    judge = Judge({"edge": edge_label(NAVIGATE), "irreversible": 0.1})
    decision, state, detail = step(judge, frame(PDF_READ["l0"]), definition=d)
    assert isinstance(decision, Act) and detail["irreversibility"] == "navigational"


def test_sensitive_windows_always_pause_before_anything_else():
    judge = Judge(allowed=0)
    decision, state, detail = step(judge, frame(BILL_EMPTY["l0"], field("c0", "amount"), sensitive=True))
    assert isinstance(decision, Pause) and decision.reason == "sensitive"


# ---- effect_seen -----------------------------------------------------------------------------------


def test_effect_seen_is_settled_by_the_l0_diff_before_any_question():
    d = with_edge({"tier": "unattended", "policy": "auto"}, TYPE_AMOUNT["id"])
    inputs = {**INPUTS, "fact:f1": {"kind": "value", "value": "1200"}}
    judge = Judge({"edge": edge_label(TYPE_AMOUNT)}, allowed=1)
    decision, state, detail = step(judge, frame(BILL_EMPTY["l0"], field("c0", "amount")), definition=d, inputs=inputs)
    assert isinstance(decision, Act) and decision.action.primitive == "type_value" and decision.action.value == "1200"
    assert state.trajectory[-1]["effect_seen"] is None
    state.n = 1
    judge = Judge({"edge": NONE})
    decision, state, detail = step(judge, frame(BILL_AMOUNT["l0"], field("c0", "amount")), state=state, definition=d, inputs=inputs)
    assert state.trajectory[0]["effect_seen"] is True and state.trajectory[0]["effect_by"] == "l0"
    assert "effect_seen" not in judge.calls[0][1]


# ---- recovery ------------------------------------------------------------------------------------


def test_recovery_closes_a_dismissable_modal_without_affirmative_clicks():
    ok_only = frame(["ctx:abc", *BILL_EMPTY["l0"]], button("c0", "ok", primary=True), modal=True, primary={"name": "ok"})
    assert not modal_is_dismissable(ok_only), "a commit-vocabulary primary button is not ours to press"
    cancel = frame(["ctx:abc", *BILL_EMPTY["l0"]], button("c0", "ok"), button("c1", "cancel"), modal=True)
    assert modal_is_dismissable(cancel) and dismiss_control(cancel).id == "c1"
    judge = Judge(allowed=0)
    decision, state, detail = step(judge, cancel)
    assert isinstance(decision, Act) and decision.action.primitive == "click" and decision.action.target.id == "c1"
    assert detail["recovery"] == "dismiss_modal" and state.recovery["used"] == 1

    nothing = frame(["ctx:abc", *BILL_EMPTY["l0"]], button("c0", "learn more"), modal=True)
    decision, state, detail = step(judge, nothing)
    assert isinstance(decision, Act) and decision.action.primitive == "press" and decision.action.args == {"key": "Escape"}

    with_textbox = frame(["ctx:abc", *BILL_EMPTY["l0"]], button("c0", "cancel"), field("c1", "reason"), modal=True)
    decision, state, detail = step(judge, with_textbox)
    assert isinstance(decision, Pause) and decision.reason == "off_plan"


def test_in_unknown_follows_the_recorded_entry_edge_then_the_budget_runs_out():
    state = PlannerState(node=BILL_EMPTY["key"])
    judge = Judge(allowed=0)
    decision, state, detail = step(judge, frame(["in:0000000000000000"]), state=state)
    assert isinstance(decision, Act) and decision.action.primitive == "navigate" and detail["recovery"] == "entry_edge"
    state.n = 1
    decision, state, detail = step(judge, frame(["in:0000000000000000"]), state=state)
    assert isinstance(decision, Act) and state.recovery["used"] == RECOVERY_BUDGET
    state.n = 2
    decision, state, detail = step(judge, frame(["in:0000000000000000"]), state=state)
    assert isinstance(decision, Pause) and decision.reason == "off_plan"
    assert detail["recoveries"] == RECOVERY_BUDGET and detail["rejudged"] == 0
    assert PlannerState.from_checkpoint(state.to_checkpoint()).recovery == state.recovery


def test_done_is_a_finish_only_on_the_goal_frame():
    judge = Judge({"edge": DONE})
    decision, state, detail = step(judge, frame(GRAPH["goal"]["l0"]))
    assert isinstance(decision, Finish) and decision.reason == "done"
    judge = Judge({"edge": NONE})
    decision, state, detail = step(judge, frame(BILL_EMPTY["l0"], field("c0", "amount")))
    assert DONE not in judge.calls[0][1]["edge"]["criteria"], "`done` is offered on the goal frame only"


# ---- the observation the worker builds from a sidecar frame ----------------------------------------


def test_worker_builds_a_v3_observation_from_the_cloud_block_only():
    result = {
        "observation": {
            "observation_id": "obs-9",
            "l0": ["in:d9689a98516bc009", "read:fact:f1"],
            "l1": {"landmarks": ["main"], "modal": False, "primary": None, "controls": ["textbox"]},
            "sensitive": False,
            "settled": True,
            "targets": [
                {
                    "id": "c0",
                    "role": "textbox",
                    "name": "amount",
                    "landmark": "form",
                    "position": "middle",
                    "aliases": [],
                    "kind": "field",
                    "has_value": False,
                    "disabled": False,
                    "primary": False,
                },
                {
                    "id": "c1",
                    "role": "button",
                    "name": "save",
                    "landmark": "form",
                    "position": "bottom",
                    "aliases": [],
                    "kind": "interactive",
                    "has_value": False,
                    "disabled": True,
                    "primary": True,
                },
            ],
        },
        "evidence": {"artifact_key": "shots/1.jpg"},
    }
    obs = observation_from_result("browser", result)
    assert obs is not None and obs.facts["l0"] == ["in:d9689a98516bc009", "read:fact:f1"] and obs.observation_id == "obs-9"
    assert [c.id for c in obs.candidates] == ["c0", "c1"] and obs.candidates[1].attrs["disabled"] is True
    assert set(obs.facts) == {"sensitive", "settled", "l0", "l1"}, "no url, title or text reaches the worker"
    assert is_v3(DEFINITION, obs)
