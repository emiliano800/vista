"""Tier 1 for the Computer Use Agent: the registry, the planner's gates, the harness
contracts and the verification step — no database, no network, no recorder. Jev is either
the stub (which must execute nothing) or a scripted judge that answers exactly the questions
the planner asks."""

from __future__ import annotations

import pytest

from vista.agents.jev import NONE, Judgment
from vista.automation.schemas import WorkflowDefinition
from vista.computer_use import tools
from vista.computer_use.harness import (
    PRIMITIVES,
    Action,
    ActionResult,
    Candidate,
    HarnessSuspended,
    Observation,
    ObserveContext,
    rank_candidates,
)
from vista.computer_use.harness_remote import RemoteHarness, action_result_from, request_for
from vista.computer_use.planner import (
    MAX_CONSECUTIVE_NONE,
    Act,
    Finish,
    Pause,
    PlannerState,
    Stop,
    plan_step,
    step_id_for,
    value_candidates,
    verify,
)
from vista.config import settings
from vista.recorder_analysis import TOOLS

RUN = "00000000-0000-0000-0000-00000000c0de"
DEFINITION = {
    "goal": "Enter the unpaid supplier invoices from the export into the sandbox invoice form, field for field.",
    "required_inputs": ["Supplier invoices export", "Sandbox form URL"],
    "allowed_tools": ["read_source_records", "map_fields", "write_destination_records", "compare_with_manual_entry"],
    "success_criteria": [
        "Every value written to the form equals the source value",
        "No invoice the employee would not have entered is submitted",
    ],
    "environment": "sandbox",
    "limits": {"max_steps": 10, "max_runtime_seconds": 300, "max_cost_usd": "1.00"},
}
INPUTS = {
    "Supplier invoices export": {"kind": "document", "submission_id": "s1", "artifact_id": "a1", "filename": "supplier_invoices.csv"},
    "Sandbox form URL": {"kind": "value", "value": "http://localhost:8765/_sandbox/entry.html"},
}
PRIMS = tools.primitives_for(DEFINITION["allowed_tools"])
LIMITS = {"steps": 10, "seconds": 300}


def page(*names: tuple[str, str]) -> Observation:
    return Observation(
        "browser",
        {"url": "http://localhost:8765/_sandbox/entry.html", "title": "Sandbox"},
        [
            Candidate(f"el-{i}", role, name, "field" if role == "textbox" else "interactive", {"harness": "browser"})
            for i, (role, name) in enumerate(names)
        ],
        observation_id="obs-1",
    )


def docs_page() -> Observation:
    return Observation(
        "documents",
        {},
        [
            Candidate(
                "doc:Supplier invoices export",
                "document",
                "Supplier invoices export (supplier_invoices.csv)",
                "document",
                {"harness": "documents", "input": "Supplier invoices export"},
            )
        ],
    )


class ScriptedJudge:
    """Answers the planner's questions from a script, one spec per call, and remembers every state it saw."""

    def __init__(self, *specs: dict):
        self.specs = list(specs)
        self.states: list[dict] = []
        self.questions: list[dict] = []

    def __call__(self, state, questions):
        self.states.append(state)
        self.questions.append(questions)
        spec = self.specs.pop(0)
        answers: dict = {}
        if "next_action" in questions:
            action = spec.get("action", NONE)
            probs = {k: 0.0 for k in questions["next_action"]["criteria"]}
            probs[action] = spec.get("p_action", 0.95)
            answers["next_action"] = {"type": "choice", "choice": action, "confidence": spec.get("p_action", 0.95), "probabilities": probs}
            answers["irreversible"] = {"type": "noul", "noul": spec.get("irreversible", 0.05)}
        if "target" in questions:
            label = spec.get("target", NONE)
            probs = {k: 0.0 for k in questions["target"]["criteria"]}
            probs[label] = spec.get("p_target", 0.9)
            answers["target"] = {"type": "choice", "choice": label, "confidence": spec.get("p_target", 0.9), "probabilities": probs}
        if "value" in questions:
            label = spec.get("value", NONE)
            probs = {k: 0.0 for k in questions["value"]["criteria"]}
            probs[label] = spec.get("p_value", 0.9)
            answers["value"] = {"type": "choice", "choice": label, "confidence": spec.get("p_value", 0.9), "probabilities": probs}
        for key, q in questions.items():
            if key.startswith("criterion_") or key == "goal_met":
                answers[key] = {"type": "noul", "noul": spec.get(key, spec.get("verify", 0.9))}
        return Judgment(model="jev-1.13.0", answers=answers, input_tokens=spec.get("tokens", 800))


def step(judge, observation, *, state=None, mode="sandbox", inputs=INPUTS, definition=DEFINITION, primitives=PRIMS, risk=0.3):
    state = state or PlannerState()
    decision, judgment, detail = plan_step(
        state,
        definition,
        inputs,
        observation,
        run_id=RUN,
        primitives=primitives,
        limits_left=LIMITS,
        mode=mode,
        risk_threshold=risk,
        judge_fn=judge,
    )
    return decision, state, detail


# ---- registry ------------------------------------------------------------------------------------


def test_every_tool_a_draft_can_request_is_in_the_registry_and_maps_to_real_primitives():
    for kind, names in TOOLS.items():
        assert tools.unmapped(names) == [], kind
        for p in tools.primitives_for(names):
            assert p in PRIMITIVES or p == NONE
    assert tools.unmapped(["read_invoice", "create_task"]) == ["read_invoice"]
    assert tools.kinds_for(["write_destination_records"]) == {"browser", "desktop"}
    assert tools.kinds_for(["report_differences", "route_for_approval"]) == set()
    assert {"done", "ask_human", "none"} <= tools.primitives_for([])
    # draft_reply may type but never submit; record_decision may submit (and therefore always pauses)
    assert "submit" not in tools.primitives_for(["draft_reply"]) and "submit" in tools.primitives_for(["record_decision"])


def test_candidates_are_ranked_deduped_and_capped():
    many = [Candidate(f"e{i}", "button", f"Button {i}", "interactive") for i in range(50)]
    many += [Candidate("f", "textbox", "Vendor", "field"), Candidate("dup", "button", "button 3", "interactive")]
    ranked = rank_candidates(many)
    assert len(ranked) == 40 and ranked[0].name == "Vendor"  # fields first, cap 40
    assert sum(1 for c in ranked if c.name.lower() == "button 3") == 1
    assert step_id_for(RUN, 3) == step_id_for(RUN, 3) != step_id_for(RUN, 4)


# ---- the gates -------------------------------------------------------------------------------------


def test_stub_jev_never_acts_and_pauses_after_two_blank_answers(monkeypatch):
    monkeypatch.setattr(settings, "typesafe_api_key", None)
    monkeypatch.delenv("VISTA_JEV_CASSETTE", raising=False)
    from vista.agents.jev import judge

    state = PlannerState()
    first, state, _ = step(judge, page(("button", "Submit invoice")), state=state)
    assert isinstance(first, Act) and first.action.primitive == "wait"  # a wait is the only thing a blank answer may do
    second, state, _ = step(judge, page(("button", "Submit invoice")), state=state)
    assert isinstance(second, Pause) and second.reason == "no_action"
    assert state.consecutive_none == MAX_CONSECUTIVE_NONE


def test_read_then_done_then_verification_from_the_final_observation_only():
    judge = ScriptedJudge(
        {"action": "read", "target": "document: Supplier invoices export (supplier_invoices.csv)"},
        {"action": "done"},
        {"goal_met": 0.9, "criterion_0": 0.95, "criterion_1": 0.8},
    )
    decision, state, detail = step(judge, docs_page())
    assert isinstance(decision, Act) and decision.action.primitive == "read"
    assert decision.action.target.id == "doc:Supplier invoices export"
    assert detail["target"].startswith("document:") and detail["p_action"] == 0.95
    state.n = 1
    state.facts["step 1"] = {"rows": [{"SUPPLIER_NAME": "Acme", "INVOICE_TOTAL": "120.00"}]}
    decision, state, _ = step(judge, docs_page(), state=state)
    assert isinstance(decision, Finish) and decision.reason == "done"
    verification = verify(DEFINITION, page(("button", "Export CSV")), state.facts, judge_fn=judge)
    assert verification.passed and verification.p_goal == 0.9 and [c["met"] for c in verification.criteria] == [True, True]
    assert "history" not in judge.states[-1] and judge.states[-1]["facts_gathered"] == state.facts  # independent read-back
    assert set(judge.questions[-1]) == {"goal_met", "criterion_0", "criterion_1"}


def test_submit_always_pauses_and_only_an_approval_for_that_step_lets_it_through():
    obs = page(("button", "Submit invoice"), ("button", "Clear form"))
    decision, state, _ = step(ScriptedJudge({"action": "submit", "target": "button: Submit invoice", "irreversible": 0.9}), obs)
    assert isinstance(decision, Pause) and decision.reason == "irreversible"
    assert decision.request["target"]["label"] == "button: Submit invoice" and decision.request["risk"]["irreversible"] == 0.9
    assert decision.request["chosen"] == "el-0" and [c["label"] for c in decision.request["candidates"]] == [
        "button: Submit invoice",
        "button: Clear form",
    ]
    step_id = decision.step_id
    approved = PlannerState(decisions={step_id: {"decision": "approve"}})
    decision, _, _ = step(ScriptedJudge({"action": "submit", "target": "button: Submit invoice", "irreversible": 0.9}), obs, state=approved)
    assert isinstance(decision, Act) and decision.gated and decision.action.primitive == "submit"
    denied = PlannerState(decisions={step_id: {"decision": "deny"}})
    decision, _, _ = step(ScriptedJudge({"action": "submit", "target": "button: Submit invoice", "irreversible": 0.9}), obs, state=denied)
    assert isinstance(decision, Stop) and decision.reason == "denied"
    # An approval for a different step does not carry over.
    other = PlannerState(decisions={step_id_for(RUN, 9): {"decision": "approve"}})
    decision, _, _ = step(ScriptedJudge({"action": "submit", "target": "button: Submit invoice", "irreversible": 0.9}), obs, state=other)
    assert isinstance(decision, Pause)


def test_risk_threshold_pauses_an_ordinary_click_and_dry_run_stops_before_any_write():
    obs = page(("button", "Post"))
    decision, _, _ = step(ScriptedJudge({"action": "click", "target": "button: Post", "irreversible": 0.4}), obs)
    assert isinstance(decision, Pause) and decision.reason == "irreversible"
    decision, _, _ = step(ScriptedJudge({"action": "click", "target": "button: Post", "irreversible": 0.4}), obs, risk=0.5)
    assert isinstance(decision, Act) and not decision.gated
    decision, _, _ = step(ScriptedJudge({"action": "click", "target": "button: Post", "irreversible": 0.1}), obs, mode="dry_run")
    assert isinstance(decision, Finish) and decision.reason == "dry_run_stopped_before_write"


def test_targets_and_values_come_only_from_candidates_and_declared_inputs():
    obs = page(("textbox", "Supplier name"), ("button", "Submit invoice"))
    # no target chosen → pause, never a guess
    decision, _, _ = step(ScriptedJudge({"action": "click", "target": NONE}), obs)
    assert isinstance(decision, Pause) and decision.reason == "ambiguous_target"
    # a low-confidence target is the same as none
    decision, _, _ = step(ScriptedJudge({"action": "click", "target": "button: Submit invoice", "p_target": 0.4}), obs)
    assert isinstance(decision, Pause) and decision.reason == "ambiguous_target"
    # typing needs a declared value; the URL input is the only value here
    decision, _, _ = step(ScriptedJudge({"action": "type_value", "target": "textbox: Supplier name", "value": NONE}), obs)
    assert isinstance(decision, Pause) and decision.reason == "missing_value"
    state = PlannerState(facts={"step 1": {"rows": [{"SUPPLIER_NAME": "Acme Fasteners"}]}})
    values = value_candidates(INPUTS, state.facts)
    assert values["input: Sandbox form URL"] == ("Sandbox form URL", "http://localhost:8765/_sandbox/entry.html")
    assert values["step 1 row 1 · SUPPLIER_NAME"] == ("step 1:row1:SUPPLIER_NAME", "Acme Fasteners")
    decision, _, detail = step(
        ScriptedJudge({"action": "type_value", "target": "textbox: Supplier name", "value": "step 1 row 1 · SUPPLIER_NAME"}),
        obs,
        state=state,
    )
    assert isinstance(decision, Act)
    assert decision.action.value == "Acme Fasteners" and decision.action.value_input == "step 1:row1:SUPPLIER_NAME"
    assert detail["value_input"] == "step 1:row1:SUPPLIER_NAME" and "Acme" not in str(detail)  # the ledger never carries the value
    # navigate takes its URL from a declared value, or pauses
    decision, _, _ = step(ScriptedJudge({"action": "navigate", "value": "input: Sandbox form URL"}), Observation("browser", {}, []))
    assert isinstance(decision, Act) and decision.action.args == {"url": "http://localhost:8765/_sandbox/entry.html"}
    decision, _, _ = step(
        ScriptedJudge({"action": "navigate", "value": "step 1 row 1 · SUPPLIER_NAME"}), Observation("browser", {}, []), state=state
    )
    assert isinstance(decision, Pause) and decision.reason == "missing_value"


def test_primitives_outside_the_allowed_tools_or_below_confidence_are_not_acted_on():
    obs = page(("button", "Post"))
    decision, state, _ = step(ScriptedJudge({"action": "http_get", "target": "button: Post"}), obs)  # not unlocked by these tools
    assert isinstance(decision, Act) and decision.action.primitive == "wait" and state.consecutive_none == 1
    decision, state, _ = step(ScriptedJudge({"action": "click", "target": "button: Post", "p_action": 0.3}), obs, state=state)
    assert isinstance(decision, Pause) and decision.reason == "no_action"
    decision, _, _ = step(ScriptedJudge({"action": "ask_human"}), obs)
    assert isinstance(decision, Pause) and decision.reason == "ask_human"


# ---- remote harness contract --------------------------------------------------------------------------


class FakeSession:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)

    def flush(self):
        for row in self.added:
            row.id = row.id or None


class FakeRun:
    id = RUN


def test_remote_harness_files_one_step_then_replays_the_recorder_answer():
    session = FakeSession()
    harness = RemoteHarness("browser", session, FakeRun(), None, replay={}, workflow_name="Test", timeout_s=180, limits_left=LIMITS)
    ctx = ObserveContext(step_id_for(RUN, 1), 1, DEFINITION["goal"], INPUTS)
    with pytest.raises(HarnessSuspended) as filed:
        harness.observe(ctx)
    assert filed.value.step_id == step_id_for(RUN, 1) and filed.value.request["action"] == "observe"
    assert session.added[0].seq == 1 and session.added[0].harness == "browser" and session.added[0].status == "pending"
    answer = {
        "ok": True,
        "description": "Looked at the page",
        "observation": {
            "observation_id": "obs-7",
            "url": "http://localhost:8765/_sandbox/entry.html",
            "title": "Sandbox",
            "candidates": [{"id": 41, "role": "button", "name": "Submit invoice", "kind": "clickable"}],
        },
    }
    replayed = RemoteHarness(
        "browser", session, FakeRun(), None, replay={1: answer}, workflow_name="Test", timeout_s=180, limits_left=LIMITS
    )
    obs = replayed.observe(ctx)
    assert (
        obs.observation_id == "obs-7"
        and obs.candidates[0].label == "button: Submit invoice"
        and obs.candidates[0].attrs["harness"] == "browser"
    )
    action = Action(step_id_for(RUN, 2), 2, "type_value", target=obs.candidates[0], value_input="input: x", value="Acme")
    req = request_for("browser", action, "obs-7", LIMITS, "Test", RUN, 180)
    assert (
        req["action"] == "type"
        and req["value"] == "Acme"
        and req["target_id"] == "41"
        and req["observation_id"] == "obs-7"
        and req["replace"] is True
    )
    result = action_result_from(
        "browser", action.step_id, {"ok": True, "description": "Typed", "result": {"url_after": "u", "previous_value": ""}, "error": None}
    )
    assert isinstance(result, ActionResult) and result.ok and result.facts == {"url": "u", "previous_value": ""}
    failed = action_result_from("browser", action.step_id, {"ok": False, "error": {"code": "private_window", "message": "refused"}})
    assert not failed.ok and failed.error == "private_window"


def test_prefilled_drafts_still_validate_and_unlock_only_their_primitives():
    definition = WorkflowDefinition.model_validate(DEFINITION)
    assert tools.unmapped(definition.allowed_tools) == []
    assert "create_task" not in tools.primitives_for(definition.allowed_tools)
