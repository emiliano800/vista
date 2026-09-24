"""Tier 1 for the graph planner: with a `PlanGraph` in the definition, Jev only chooses among the
moves the recordings observed from the state the run is in; absence pauses; every traversal comes
back as a graph delta. No database, no network."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from taskmining.state import merge_graphs
from vista.agents.jev import NONE, Judgment
from vista.automation.schemas import PlanGraph, WorkflowDefinition
from vista.computer_use.graph import DONE, Graph, edge_label, merge_run, node_label, node_offers, observed_role, plan_graph_step
from vista.computer_use.harness import Candidate, Observation
from vista.computer_use.planner import Act, Finish, Pause, PlannerState, Stop, step_id_for

GRAPH = json.loads((Path(__file__).parent / "fixtures" / "plan_invoice.json").read_text())
RUN = "00000000-0000-0000-0000-00000000c0de"
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
NODES = {n["key"]: n for n in GRAPH["nodes"]}
EDGES = {e["id"]: e for e in GRAPH["edges"]}


def edge(action: str, slot: str | None = None) -> dict:
    return next(e for e in GRAPH["edges"] if e["action_class"] == action and (slot is None or e["slot"] == slot))


def pdf() -> Observation:
    doc = Candidate("doc:invoice", "document", "invoice (INV-1042.pdf)", "document", {"input": "invoice"})
    return Observation("documents", {"inputs": ["invoice"]}, [doc])


def books(*names: tuple[str, str]) -> Observation:
    return Observation(
        "browser",
        {"url": "http://localhost:8765/_sandbox/bill.html", "title": "QuickBooks Sandbox — Bills"},
        [Candidate(f"el-{i}", role, name, "field" if role == "textbox" else "interactive") for i, (role, name) in enumerate(names)],
    )


class ScriptedJudge:
    def __init__(self, *specs: dict):
        self.specs = list(specs)
        self.states: list[dict] = []
        self.questions: list[dict] = []

    def __call__(self, state, questions):
        self.states.append(state)
        self.questions.append(questions)
        spec = self.specs.pop(0)
        answers: dict = {}

        def choose(q: str, label, p):
            probs = {k: 0.0 for k in questions[q]["criteria"]}
            assert label in probs, f"{q}: {label!r} was never offered; offered {list(probs)}"
            probs[label] = p
            probs.update(spec.get(f"{q}_also", {}))
            answers[q] = {"type": "choice", "choice": label, "confidence": p, "probabilities": probs}

        if "effect_seen" in questions:
            answers["effect_seen"] = {"type": "noul", "noul": spec.get("effect_seen", 0.9)}
        if "node" in questions:
            first = next(iter(questions["node"]["criteria"]))
            choose("node", spec.get("node", first), spec.get("p_node", 0.9))
        if "edge" in questions:
            choose("edge", spec.get("edge", NONE), spec.get("p_edge", 0.9))
        if "irreversible" in questions:
            answers["irreversible"] = {"type": "noul", "noul": spec.get("irreversible", 0.05)}
        for q in questions:  # one target question per offered edge; the spec's target answers all of them it fits
            if q.startswith("target:"):
                wanted = spec.get("target", NONE)
                label = wanted if wanted in questions[q]["criteria"] else NONE
                choose(q, label, spec.get("p_target", 0.9) if label == wanted else 0.9)
        if "value" in questions:
            choose("value", spec.get("value", NONE), spec.get("p_value", 0.9))
        return Judgment(model="jev-1.13.0", answers=answers, input_tokens=700)


def step(judge, observation, *, state=None, mode="sandbox", inputs=INPUTS, definition=DEFINITION):
    state = state or PlannerState()
    decision, judgment, detail = plan_graph_step(
        state, definition, inputs, observation, run_id=RUN, primitives=PRIMS, limits_left=LIMITS, mode=mode, judge_fn=judge
    )
    return decision, state, detail


def test_the_fixture_definition_validates_and_roles_are_read_off_observations():
    WorkflowDefinition.model_validate(DEFINITION)
    assert observed_role(pdf()) == "pdf"
    # The worker's composed observation (no remote screen) is harness "local": the role still comes off the documents.
    local_doc = Candidate("doc:invoice", "document", "invoice (INV-1042.pdf)", "document", {"input": "invoice", "harness": "documents"})
    assert observed_role(Observation("local", {"documents_inputs": ["invoice"]}, [local_doc])) == "pdf"
    endpoint = Candidate("http:erp:/bills", "endpoint", "erp /bills", "endpoint", {"harness": "http"})
    assert observed_role(Observation("local", {}, [endpoint])) == "browser"
    assert observed_role(Observation("local", {}, [])) == "documents"
    assert observed_role(books(("button", "Save"))) == "accounting"
    assert observed_role(Observation("browser", {"title": "Some site"}, [])) == "browser"


def test_first_step_is_located_on_a_start_state_and_offered_only_that_states_moves():
    read = edge("read")
    judge = ScriptedJudge({"edge": edge_label(read), "target": "document: invoice (INV-1042.pdf)"})
    decision, state, detail = step(judge, pdf())
    assert isinstance(decision, Act) and decision.action.primitive == "read"
    offered_nodes = judge.states[0]["plan"]["where_i_may_be"]
    assert offered_nodes[0] == node_label(NODES[GRAPH["start"][0]]) and all("pdf" in n for n in offered_nodes)
    offered_moves = judge.states[0]["plan"]["moves_recorded_from_here"]
    pdf_nodes = {n["key"] for n in GRAPH["nodes"] if n["app_role"] == "pdf"}
    assert offered_moves[0] == edge_label(read)
    assert offered_moves == [edge_label(e) for e in GRAPH["edges"] if e["frm"] in pdf_nodes]  # moves out of pdf states only
    edge_criteria = judge.questions[0]["edge"]["criteria"]
    assert NONE in edge_criteria and DONE not in edge_criteria  # no offered state is one the recordings ended in
    assert "effect_seen" not in judge.questions[0]
    assert detail["planner"] == "graph" and detail["edge"] == read["id"] and detail["policy"] == "auto"
    assert state.node == read["to"]
    assert state.trajectory == [{"edge": read["id"], "step_id": detail["step_id"], "seq": 1, "effect_seen": None}]
    assert "history" not in judge.states[0] and "primitives" not in judge.states[0]  # the graph replaces the open vocabulary


def test_a_slot_that_is_a_declared_input_is_resolved_by_code_and_a_fact_slot_only_from_its_producer():
    read = edge("read")
    fill_fact = edge("type_value", "f1")
    fill_input = edge("type_value", "input_1")
    after_read = PlannerState(n=1, node=read["to"], trajectory=[{"edge": read["id"], "step_id": "s1", "seq": 1, "effect_seen": None}])
    after_read.history.append({"step_id": "s1", "executed": True})
    after_read.facts["step 1"] = {"rows": [{"Total": "1,250.00"}]}
    nav = edge("navigate")
    judge = ScriptedJudge({"effect_seen": 0.95, "edge": edge_label(nav)})
    decision, state, detail = step(judge, pdf(), state=after_read)
    assert (
        isinstance(decision, Act) and decision.action.primitive == "navigate" and decision.action.args["url"].startswith("http://localhost")
    )
    assert state.trajectory[0]["effect_seen"] is True and detail["effect_seen"] is True
    state.n = 2
    state.history.append({"step_id": detail["step_id"], "executed": True})

    obs = books(("textbox", "Amount"), ("textbox", "Vendor"), ("button", "Save"))
    # click edge, then the paste of the copied total into Amount
    click = edge("click")
    judge = ScriptedJudge({"edge": edge_label(click), "target": "button: Save", "irreversible": 0.05})
    decision, state, detail = step(judge, obs, state=state)
    assert isinstance(decision, Pause) and decision.reason == "confirm" and decision.request["policy"] == "confirm"  # a recorded write asks
    assert state.proposals == [{"edge": click["id"], "step_id": detail["step_id"]}]
    state.decisions[detail["step_id"]] = {"decision": "approve"}
    judge = ScriptedJudge({"edge": edge_label(click), "target": "button: Save", "irreversible": 0.05})
    decision, state, detail = step(judge, obs, state=state)
    assert isinstance(decision, Act) and decision.gated and decision.action.primitive == "click"
    state.n = 3
    state.history.append({"step_id": detail["step_id"], "executed": True})

    before_fill = copy.deepcopy(state)
    judge = ScriptedJudge({"edge": edge_label(fill_fact), "target": "textbox: Amount", "value": "step 1 row 1 · Total"})
    state.decisions[step_id_of(state)] = {"decision": "approve"}
    decision, state, detail = step(judge, obs, state=state)
    assert isinstance(decision, Act) and decision.action.value == "1,250.00" and decision.action.value_input == "step 1:row1:Total"
    state.n = 4
    state.history.append({"step_id": detail["step_id"], "executed": True})

    # A value from a step whose edge did not produce the slot is refused even if Jev picks it.
    before_fill.facts["step 3"] = {"rows": [{"Total": "999"}]}
    judge = ScriptedJudge({"edge": edge_label(fill_fact), "target": "textbox: Amount", "value": "step 3 row 1 · Total"})
    before_fill.decisions[step_id_of(before_fill)] = {"decision": "approve"}
    decision, _, _ = step(judge, obs, state=before_fill)
    assert isinstance(decision, Pause) and decision.reason == "missing_value"

    # A declared input slot never asks Jev for the value.
    judge = ScriptedJudge({"edge": edge_label(fill_input), "target": "textbox: Vendor", "value": NONE})
    state.decisions[step_id_of(state)] = {"decision": "approve"}
    decision, state, _ = step(judge, obs, state=state)
    assert isinstance(decision, Act) and decision.action.value == "ACME" and decision.action.value_input == "input_1"


def step_id_of(state: PlannerState) -> str:
    return step_id_for(RUN, state.n + 1)


def test_submit_edges_always_pause_and_done_finishes():
    submit = edge("submit")
    state = PlannerState(n=5, node=submit["frm"])
    obs = books(("button", "Save"))
    spec = {"edge": edge_label(submit), "target": "button: Save", "irreversible": 0.9}
    decision, state, detail = step(ScriptedJudge(spec), obs, state=state)
    assert isinstance(decision, Pause) and decision.reason == "irreversible" and detail["policy"] == "always_ask"
    state.decisions[decision.step_id] = {"decision": "deny"}
    decision, state, _ = step(ScriptedJudge(spec), obs, state=state)
    assert isinstance(decision, Stop)
    done_state = PlannerState(n=6, node=submit["to"])
    decision, _, detail = step(ScriptedJudge({"edge": DONE}), obs, state=done_state)
    assert isinstance(decision, Finish) and decision.reason == "done" and detail["next_action"] == "done"


def test_the_target_is_judged_per_edge_over_the_kinds_that_edge_can_act_on():
    # "Which control?" is asked once per offered move, naming that move's recorded control, and only over
    # candidates of a kind the move can act on — so a submit is never offered the textbox it just filled
    # and a type edge is never offered a button.
    submit = edge("submit")
    obs = books(("textbox", "Invoice number"), ("button", "Save"), ("button", "Clear"))
    judge = ScriptedJudge({"edge": edge_label(submit), "target": "button: Save", "irreversible": 0.9})
    decision, _, detail = step(judge, obs, state=PlannerState(n=5, node=submit["frm"]))
    assert isinstance(decision, Pause) and decision.reason == "irreversible" and detail["target"] == "button: Save"
    questions = judge.questions[0]
    assert "target" not in questions
    q = questions[f"target:{submit['id']}"]
    assert set(q["criteria"]) == {"button: Save", "button: Clear", NONE} and submit["control"] in q["instructions"]
    typing = edge("type_value", "input_1")
    judge = ScriptedJudge({"edge": edge_label(typing), "target": "button: Save", "p_target": 0.7})
    decision, _, detail = step(judge, obs, state=PlannerState(n=2, node=typing["frm"]))
    assert isinstance(decision, Pause) and decision.reason == "ambiguous_target" and detail["target"] is None
    assert set(judge.questions[0][f"target:{typing['id']}"]["criteria"]) == {"textbox: Invoice number", NONE}


def test_off_plan_is_defined_not_judged():
    # No state of the observed role: pause at once.
    chat = Observation("desktop", {"app": "Slack", "title": "general"}, [Candidate("w1", "textbox", "Message")])
    decision, _, detail = step(ScriptedJudge({"node": NONE, "edge": NONE}), chat)
    assert isinstance(decision, Pause) and decision.reason == "off_plan" and detail["node"] is None
    # A state, but Jev sees none of its moves on screen: wait once, then pause.
    obs = books(("button", "Something else"))
    state = PlannerState(n=2, node=edge("click")["frm"])
    decision, state, _ = step(ScriptedJudge({"edge": NONE}), obs, state=state)
    assert isinstance(decision, Act) and decision.action.primitive == "wait"
    decision, state, _ = step(ScriptedJudge({"edge": NONE}), obs, state=state)
    assert isinstance(decision, Pause) and decision.reason == "off_plan"
    # A move whose control is not on screen is an ambiguous target, never a guess.
    state = PlannerState(n=2, node=edge("click")["frm"])
    decision, _, _ = step(ScriptedJudge({"edge": edge_label(edge("click")), "target": NONE}), obs, state=state)
    assert isinstance(decision, Pause) and decision.reason == "ambiguous_target"
    # Moves are only ever the graph's own edges: an edge from another state is not offered.
    offers = node_offers(PlannerState(n=2, node=edge("click")["frm"]), Graph.from_definition(DEFINITION), obs)
    assert all(n["app_role"] == "accounting" for n in offers)


def test_dry_run_stops_before_the_first_recorded_write():
    state = PlannerState(n=2, node=edge("click")["frm"])
    judge = ScriptedJudge({"edge": edge_label(edge("click")), "target": "button: Save"})
    decision, _, _ = step(judge, books(("button", "Save")), state=state, mode="dry_run")
    assert isinstance(decision, Finish) and decision.reason == "dry_run_stopped_before_write"


def test_a_run_comes_back_as_a_graph_delta_that_merges_into_a_draft_without_touching_the_version():
    read, nav, click, submit = edge("read"), edge("navigate"), edge("click"), edge("submit")
    state = PlannerState(
        n=4,
        history=[{"step_id": f"s{i}", "executed": True} for i in (1, 2, 3)],
        trajectory=[
            {"edge": read["id"], "step_id": "s1", "seq": 1, "effect_seen": True},
            {"edge": nav["id"], "step_id": "s2", "seq": 2, "effect_seen": True},
            {"edge": click["id"], "step_id": "s3", "seq": 3, "effect_seen": False},
        ],
        proposals=[{"edge": click["id"], "step_id": "s3"}, {"edge": submit["id"], "step_id": "s4"}],
        decisions={"s3": {"decision": "approve"}, "s4": {"decision": "deny"}},
    )
    delta = merge_run(DEFINITION, state, "run-1", verified=True)
    PlanGraph.model_validate(delta)
    stats = {e["action_class"]: e["stats"] for e in delta["edges"]}
    assert stats["read"] == {"support": 1, "recorded": 0, "executed": 1, "verified_ok": 1, "approved": 0, "denied": 0, "effect_missing": 0}
    assert stats["click"] == {"support": 1, "recorded": 0, "executed": 1, "verified_ok": 1, "approved": 1, "denied": 0, "effect_missing": 1}
    assert stats["submit"] == {
        "support": 0,
        "recorded": 0,
        "executed": 0,
        "verified_ok": 0,
        "approved": 0,
        "denied": 1,
        "effect_missing": 0,
    }
    click_delta = next(e for e in delta["edges"] if e["id"] == click["id"])
    assert click_delta["provenance"] == [{"source": "run", "id": "run-1", "event_ids": ["s3"]}]
    assert {p["source"] for e in delta["edges"] for p in e["provenance"]} == {"run"}
    assert delta["start"] == [read["frm"]] and "type_value" not in stats

    grown = merge_graphs([GRAPH, delta])
    PlanGraph.model_validate(grown)
    assert grown["trajectories"] == 2
    read_after = next(e for e in grown["edges"] if e["id"] == read["id"])
    assert read_after["stats"]["support"] == 2 and read_after["stats"]["executed"] == 1
    assert [p["source"] for p in read_after["provenance"]] == ["recording", "run"]
    assert next(e for e in grown["edges"] if e["id"] == submit["id"])["policy"] == "always_ask"
    assert DEFINITION["graph"] == GRAPH  # nothing mutated the approved graph
    assert merge_run({k: v for k, v in DEFINITION.items() if k != "graph"}, state, "run-1", True) is None
    assert merge_run(DEFINITION, PlannerState(), "run-1", True) is None


def test_a_move_from_another_state_is_re_asked_over_the_located_states_own_moves():
    # Jev puts the run on the "just navigated" state but picks the later typing move (its field is on
    # screen). Instead of pausing, the planner asks once more with only that state's recorded moves.
    at = edge("click")["frm"]
    later = edge("type_value", "input_1")
    obs = books(("button", "New bill"), ("textbox", "Supplier"))
    judge = ScriptedJudge(
        {"node": node_label(NODES[at]), "edge": edge_label(later), "target": "textbox: Supplier"},
        {"edge": edge_label(edge("click")), "target": "button: New bill"},
    )
    decision, state, detail = step(judge, obs, state=PlannerState(n=1, node=at))
    # (the fixture's click edge carries `policy: confirm`, so the re-asked move reaches the policy gate)
    assert isinstance(decision, Pause) and decision.reason == "confirm" and decision.request["target"]["label"] == "button: New bill"
    assert detail["rejudged"] is True and detail["edge"] == edge("click")["id"] and detail["node"] == at
    assert len(judge.states) == 2
    second = judge.states[1]["plan"]
    assert second["where_i_may_be"] == [node_label(NODES[at])]
    assert edge_label(later) not in second["moves_recorded_from_here"]
    assert all(EDGES[e["id"]]["frm"] == at for e in GRAPH["edges"] if edge_label(e) in second["moves_recorded_from_here"])
    # A second answer that still names nothing fitting falls through to the usual gates.
    judge = ScriptedJudge({"node": node_label(NODES[at]), "edge": edge_label(later), "target": "textbox: Supplier"}, {"edge": NONE})
    decision, _, detail = step(judge, obs, state=PlannerState(n=1, node=at))
    assert isinstance(decision, Act) and decision.action.primitive == "wait" and detail["rejudged"] is True


def test_done_is_only_offered_at_a_terminal_state_and_re_asked_elsewhere():
    at = edge("click")["frm"]  # terminal in the fixture
    obs = books(("button", "New bill"), ("textbox", "Amount"))
    judge = ScriptedJudge({"node": node_label(NODES[at]), "edge": DONE})
    decision, _, _ = step(judge, obs, state=PlannerState(n=1, node=at))
    assert isinstance(decision, Finish) and decision.reason == "done"
    # From a state the recordings never ended in, `done` is not among the moves; if it was offered
    # (a terminal state was also a possibility) and chosen, the planner asks again over the located state's moves.
    typing = edge("type_value", "f1")
    mid = typing["to"]
    assert not NODES[mid].get("terminal")
    judge = ScriptedJudge(
        {"node": node_label(NODES[mid]), "edge": DONE},
        {"edge": edge_label(edge("type_value", "input_1")), "target": "textbox: Amount"},
    )
    obs = books(("textbox", "Amount"))
    decision, _, detail = step(judge, obs, state=PlannerState(n=2, node=mid))
    assert detail["rejudged"] is True and detail["next_action"] == "type_value"
    assert DONE not in judge.questions[1]["edge"]["criteria"]
