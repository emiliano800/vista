"""Step 7: typed verification, autonomy tiers with cooling and FDE click, automatic demotion,
shadow scoring and the shadow report. No database, no network."""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from taskmining import tiers
from taskmining.state import merge_graphs
from tests.test_run_v3 import BILL_EMPTY, DEFINITION, GRAPH, INPUTS, NAVIGATE, SUBMIT, TYPE_AMOUNT, Judge, field, frame, step, with_edge
from vista.agents.jev import Judgment
from vista.automation import graph_review
from vista.automation.schemas import PlanGraph, WorkflowDefinition
from vista.computer_use.graph import DONE, edge_label, merge_run
from vista.computer_use.harness_remote import action_result_from, request_for
from vista.computer_use.planner import Act, PlannerState
from vista.computer_use.run_v3 import record_read_back
from vista.computer_use.verify_v3 import verify_v3
from vista_device.drivers import Result
from vista_device.server import Sidecar

READ = next(e for e in GRAPH["edges"] if e["action_class"] == "read")
GOAL_L0 = GRAPH["goal"]["l0"]
TODAY = datetime.now(UTC).date()


def edge(**over) -> dict:
    return {
        "id": "e1",
        "frm": "a",
        "to": "b",
        "action_class": "click",
        "irreversibility": "navigational",
        "slot": None,
        "produces": [],
        "tier": "shadow",
        "stats": {"support": 0, "recorded": 0, "executed": 0, "verified_ok": 0, "approved": 0, "denied": 0, "effect_missing": 0},
        **over,
    }


def stats(**over) -> dict:
    return {"support": 0, "recorded": 0, "executed": 0, "verified_ok": 0, "approved": 0, "denied": 0, "effect_missing": 0, **over}


# ---- tiers in code -------------------------------------------------------------------------------


def test_entry_conditions_climb_one_tier_at_a_time_and_stop_at_the_ceiling():
    e = edge(stats=stats(shadow_total=3, shadow_agree=3))
    assert tiers.entry_tier(e, covered=False) == "ask"
    e = edge(stats=stats(executed=3, verified_ok=3))
    assert tiers.entry_tier(e, covered=False) == "confirm"
    e = edge(stats=stats(executed=10, verified_ok=10))
    assert tiers.entry_tier(e, covered=False) == "unattended"
    # A write without covering read-back can never pass `confirm`; a committing edge never can.
    e = edge(action_class="type_value", irreversibility="mutating", slot="field:amount", stats=stats(executed=10, verified_ok=10))
    assert tiers.entry_tier(e, covered=False) == "confirm"
    assert tiers.entry_tier(e, covered=True) == "unattended"
    e = edge(action_class="submit", irreversibility="committing", stats=stats(executed=50, verified_ok=50))
    assert tiers.entry_tier(e, covered=True) == "confirm"
    # Any denial or leakage failure pins the entry at shadow.
    assert tiers.entry_tier(edge(stats=stats(executed=10, verified_ok=10, denied=1)), covered=True) == "shadow"
    assert tiers.entry_tier(edge(stats=stats(executed=10, verified_ok=10, leakage_failed=1)), covered=True) == "shadow"


def test_proposals_are_one_tier_up_and_past_ask_need_an_fde_after_cooling():
    e = edge(tier="shadow", stats=stats(executed=10, verified_ok=10))
    p = tiers.proposal(e, True, TODAY)
    assert p["to"] == "ask" and p["needs_fde"] is False
    e = edge(tier="ask", tier_since=TODAY.isoformat(), stats=stats(executed=10, verified_ok=10))
    p = tiers.proposal(e, True, TODAY)
    assert p["to"] == "confirm" and p["needs_fde"] is True and p["cooled"] is False
    e["tier_since"] = (TODAY - timedelta(days=tiers.COOLING_DAYS)).isoformat()
    assert tiers.proposal(e, True, TODAY)["cooled"] is True
    assert tiers.proposal(edge(tier="unattended", stats=stats(executed=10, verified_ok=10)), True, TODAY) is None


def test_demotion_is_automatic_one_tier_down_for_each_named_reason():
    assert tiers.demotion(edge(tier="unattended", stats=stats(denied=1))) == ("confirm", "denied > 0: a person denied the move")
    assert tiers.demotion(edge(tier="confirm", stats=stats(verified_fail=1)))[0] == "ask"
    assert tiers.demotion(edge(tier="ask", stats=stats(leakage_failed=1)))[0] == "shadow"
    assert tiers.demotion(edge(tier="shadow", stats=stats(denied=3))) is None  # nowhere lower
    # effect_missing demotes writes only.
    assert tiers.demotion(edge(tier="confirm", stats=stats(effect_missing=1))) is None
    assert (
        tiers.demotion(edge(tier="confirm", action_class="type_value", irreversibility="mutating", stats=stats(effect_missing=1)))[0]
        == "ask"
    )


def test_covering_read_back_names_the_slot_the_edge_writes():
    e = edge(action_class="type_value", slot="field:amount")
    assert tiers.covering_read_back(e, [{"type": "read_back", "slot": "field:amount"}])
    assert not tiers.covering_read_back(e, [{"type": "present", "slot": "field:amount"}])
    assert not tiers.covering_read_back(e, [{"type": "read_back", "slot": "field:vendor"}])


def test_merging_graphs_keeps_the_stricter_tier_and_sums_the_new_stats_without_touching_structure():
    a = copy.deepcopy(GRAPH)
    b = copy.deepcopy(GRAPH)
    ea = next(e for e in a["edges"] if e["id"] == NAVIGATE["id"])
    eb = next(e for e in b["edges"] if e["id"] == NAVIGATE["id"])
    ea["tier"], ea["tier_since"] = "unattended", "2026-09-01"
    eb["tier"], eb["tier_since"] = "ask", "2026-09-10"
    eb["stats"]["shadow_total"] = 2
    merged = next(e for e in merge_graphs([a, b])["edges"] if e["id"] == NAVIGATE["id"])
    assert merged["tier"] == "ask" and merged["tier_since"] == "2026-09-01"
    assert merged["stats"]["shadow_total"] == 2 and merged["stats"]["support"] == 2 * NAVIGATE["stats"]["support"]
    assert merged["descriptor"] == NAVIGATE["descriptor"] and merged["irreversibility"] == NAVIGATE["irreversibility"]


def test_schema_pins_the_tier_and_hides_zero_tier_stats():
    d = with_edge({"tier": "confirm", "tier_since": "2026-09-01"}, NAVIGATE["id"])
    dumped = WorkflowDefinition.model_validate(d).model_dump(mode="json")
    e = next(e for e in dumped["graph"]["edges"] if e["id"] == NAVIGATE["id"])
    assert e["tier"] == "confirm" and e["tier_since"] == "2026-09-01"
    assert "shadow_total" not in e["stats"], "zero tier stats leave the serialised graph unchanged"
    with pytest.raises(ValueError):
        WorkflowDefinition.model_validate(with_edge({"tier": "unattended"}, SUBMIT["id"]))


# ---- shadow in the run loop --------------------------------------------------------------------


def test_shadow_tier_performs_nothing_and_scores_the_employee_s_move_against_the_proposal():
    d = with_edge({"tier": "shadow"}, TYPE_AMOUNT["id"])
    inputs = {**INPUTS, "fact:f1": {"kind": "value", "value": "1200"}}
    judge = Judge({"edge": edge_label(TYPE_AMOUNT)})
    decision, state, detail = step(judge, frame(BILL_EMPTY["l0"], field("c0", "amount")), definition=d, inputs=inputs)
    assert isinstance(decision, Act) and decision.action.primitive == "wait" and detail["shadow"] is True
    assert state.recovery["shadow_pending"]["edge"] == TYPE_AMOUNT["id"]
    assert state.proposals[-1]["shadow"] is True
    # The employee typed the amount: the screen is now the edge's destination → agreement.
    state.history.append({"step_id": detail["step_id"], "executed": True})
    to_node = next(n for n in GRAPH["nodes"] if n["key"] == TYPE_AMOUNT["to"])
    _, state, detail = step(Judge(), frame(to_node["l0"], field("c0", "amount")), state=state, definition=d, inputs=inputs)
    assert detail["shadow_agree"] is True
    assert state.recovery["shadow"] == [{"edge": TYPE_AMOUNT["id"], "from": BILL_EMPTY["key"], "acted": TYPE_AMOUNT["to"], "agree": True}]
    assert "shadow_pending" not in state.recovery


def test_shadow_disagreement_records_where_the_employee_went_instead():
    d = with_edge({"tier": "shadow"}, TYPE_AMOUNT["id"])
    inputs = {**INPUTS, "fact:f1": {"kind": "value", "value": "1200"}}
    _, state, detail = step(
        Judge({"edge": edge_label(TYPE_AMOUNT)}), frame(BILL_EMPTY["l0"], field("c0", "amount")), definition=d, inputs=inputs
    )
    _, state, detail = step(Judge(), frame(GOAL_L0), state=state, definition=d, inputs=inputs)
    assert detail["shadow_agree"] is False
    assert state.recovery["shadow"][0]["acted"] == GRAPH["goal"]["node"]
    delta = merge_run(d, state, "run-1", None)
    s = next(e for e in delta["edges"] if e["id"] == TYPE_AMOUNT["id"])["stats"]
    assert s["shadow_total"] == 1 and "shadow_agree" not in s and s["executed"] == 0


# ---- typed verification ------------------------------------------------------------------------


def test_present_criteria_are_decided_in_code_on_the_goal_frame_without_jev():
    state = PlannerState(trajectory=[{"edge": NAVIGATE["id"], "step_id": "s1"}], node=GRAPH["goal"]["node"])
    v = verify_v3(DEFINITION, frame(GOAL_L0), state, judge_fn=Judge(allowed=0))
    assert v.goal_met and v.passed
    assert {c["slot"] for c in v.criteria} == {"field:amount", "field:c2", "fact:f1"} and all(c["by"] == "code" for c in v.criteria)
    v = verify_v3(DEFINITION, frame(BILL_EMPTY["l0"]), state, judge_fn=Judge(allowed=0))
    assert not v.goal_met and not v.passed


def with_criteria(criteria: list[dict]) -> dict:
    d = copy.deepcopy(DEFINITION)
    d["graph"]["goal"]["criteria"] = criteria
    WorkflowDefinition.model_validate(d)
    return d


def test_a_read_back_is_performed_along_the_task_s_edge_before_done_is_offered_and_only_a_boolean_comes_back():
    d = with_criteria([{"type": "read_back", "slot": "fact:f1", "read_back_via": READ["id"], "read_back_delay": 2}])
    inputs = {**INPUTS, "fact:f1": {"kind": "value", "value": "1200"}}
    judge = Judge(allowed=0)
    decision, state, detail = step(judge, frame(GOAL_L0), definition=d, inputs=inputs)
    assert isinstance(decision, Act) and decision.action.primitive == "read"
    assert decision.action.args == {"read_back": "fact:f1", "delay_ms": 2000} and decision.action.value == "1200"
    assert state.recovery["read_back_pending"] == "fact:f1" and detail["read_back"] == {"slot": "fact:f1", "via": READ["id"]}
    req = request_for("browser", decision.action, "obs-1", {}, "wf", "run", 30)
    assert req["read_back"] == {"slot": "fact:f1", "expected": "1200", "delay_ms": 2000}
    # The device answers a boolean; the worker never sees the text.
    result = action_result_from("browser", detail["step_id"], {"ok": True, "result": {"read_back": {"slot": "fact:f1", "ok": True}}})
    record_read_back(state, result.facts)
    assert state.recovery["read_back"] == {"fact:f1": True} and "read_back_pending" not in state.recovery
    v = verify_v3(d, frame(GOAL_L0), state, judge_fn=Judge(allowed=0))
    assert v.passed and v.criteria == [{"type": "read_back", "slot": "fact:f1", "met": True, "by": "device"}]


def test_a_read_back_that_cannot_be_performed_is_not_met():
    d = with_criteria([{"type": "read_back", "slot": "fact:f1"}])  # no read_back_via
    inputs = {**INPUTS, "fact:f1": {"kind": "value", "value": "1200"}}
    decision, state, detail = step(Judge({"edge": DONE}), frame(GOAL_L0), definition=d, inputs=inputs)
    assert state.recovery["read_back"] == {"fact:f1": False}
    v = verify_v3(d, frame(GOAL_L0), state, judge_fn=Judge(allowed=0))
    assert not v.passed and v.criteria[0]["by"] == "device"
    # A pending answer that never arrived is not met either.
    state = PlannerState(node=GRAPH["goal"]["node"], trajectory=[{"edge": NAVIGATE["id"], "step_id": "s1"}])
    v = verify_v3(d, frame(GOAL_L0), state, judge_fn=Judge(allowed=0))
    assert not v.passed and v.criteria[0] == {"type": "read_back", "slot": "fact:f1", "met": False, "by": "unanswered"}


def test_graded_criteria_ask_jev_at_the_declared_threshold_and_send_slot_names_only():
    d = with_criteria([{"type": "graded", "slot": "field:amount", "threshold": 0.7}])
    state = PlannerState(node=GRAPH["goal"]["node"], trajectory=[{"edge": NAVIGATE["id"], "step_id": "s1"}])
    calls: list[dict] = []

    def judge(view: dict, questions: dict) -> Judgment:
        calls.append(view)
        return Judgment("jev", {q: {"type": "noul", "noul": 0.75} for q in questions})

    v = verify_v3(d, frame(GOAL_L0), state, judge_fn=judge)
    assert v.criteria == [{"type": "graded", "slot": "field:amount", "met": True, "p": 0.75, "by": "jev"}]
    assert "1200" not in str(calls) and "candidates" not in calls[0]
    d = with_criteria([{"type": "graded", "slot": "field:amount", "threshold": 0.8}])
    assert not verify_v3(d, frame(GOAL_L0), state, judge_fn=judge).passed


def test_sidecar_compares_read_back_on_device_and_drops_the_text():
    r = Result(True, "Read the text", result={"text": "Bill total: 1,200.00 EUR"})
    out = Sidecar._compare_read_back(r, {"slot": "fact:f1", "expected": "1,200.00"})
    assert out.result == {"read_back": {"slot": "fact:f1", "ok": True}}
    out = Sidecar._compare_read_back(r, {"slot": "fact:f1", "expected": "9,999"})
    assert out.result["read_back"]["ok"] is False
    assert Sidecar._compare_read_back(r, {"slot": "fact:f1", "expected": ""}).result["read_back"]["ok"] is False


# ---- review: tiers in the draft, shadow report ---------------------------------------------------


def run_with(state: PlannerState, *, verified: bool | None = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        status="succeeded" if verified else "failed",
        mode="sandbox",
        outcome={"verified": verified},
        finished_at=None,
        checkpoint=state.to_checkpoint(),
    )


def walk(edges: list[dict], *, verified: bool | None = True, deny: str | None = None, effect_seen: bool | None = True) -> SimpleNamespace:
    state = PlannerState(
        history=[{"step_id": f"s{i}", "executed": True} for i in range(len(edges))],
        trajectory=[{"edge": e["id"], "step_id": f"s{i}", "seq": i + 1, "effect_seen": effect_seen} for i, e in enumerate(edges)],
    )
    if deny:
        state.proposals = [{"edge": deny, "step_id": "p0"}]
        state.decisions = {"p0": {"decision": "deny"}}
    return run_with(state, verified=verified)


def version(definition: dict) -> SimpleNamespace:
    return SimpleNamespace(definition=definition, number=1)


def test_a_denial_demotes_the_tier_automatically_and_the_policy_follows():
    d = with_edge({"tier": "unattended", "policy": "auto"}, NAVIGATE["id"])
    draft, _, changes, proposals = graph_review._fold(version(d), [walk([NAVIGATE], deny=NAVIGATE["id"])], set())
    e = next(e for e in draft["edges"] if e["id"] == NAVIGATE["id"])
    assert e["tier"] == "confirm" and e["policy"] == "confirm" and e["tier_since"] == TODAY.isoformat()
    tier_changes = [c for c in changes if c.kind == "tier"]
    assert [(c.before, c.after) for c in tier_changes] == [("unattended", "confirm")] and "denied" in tier_changes[0].reason
    assert proposals == []
    PlanGraph.model_validate(draft)


def test_a_failed_verification_demotes_a_confirm_edge_to_ask():
    d = with_edge({"tier": "confirm"}, NAVIGATE["id"])
    draft, _, changes, _ = graph_review._fold(version(d), [walk([NAVIGATE], verified=False)], set())
    assert next(e for e in draft["edges"] if e["id"] == NAVIGATE["id"])["tier"] == "ask"
    assert any(c.kind == "tier" and "verified_fail" in c.reason for c in changes)


def test_promotion_past_ask_is_an_fde_click_after_cooling_and_shadow_to_ask_is_not():
    cooled = (TODAY - timedelta(days=tiers.COOLING_DAYS)).isoformat()
    d = with_edge({"tier": "ask", "tier_since": cooled, "policy": "confirm"}, NAVIGATE["id"])
    runs = [walk([NAVIGATE]) for _ in range(3)]
    _, _, _, proposals = graph_review._fold(version(d), runs, set())
    (p,) = proposals
    assert (p.from_tier, p.to_tier, p.needs_fde, p.cooled) == ("ask", "confirm", True, True)
    with pytest.raises(HTTPException) as e:
        graph_review._fold(version(d), runs, {NAVIGATE["id"]})
    assert e.value.status_code == 403
    draft, _, changes, _ = graph_review._fold(version(d), runs, {NAVIGATE["id"]}, fde=True)
    assert next(e for e in draft["edges"] if e["id"] == NAVIGATE["id"])["tier"] == "confirm"
    assert any(c.kind == "tier" and c.reason == "promoted by FDE" for c in changes)
    # Still cooling: even the FDE waits.
    hot = with_edge({"tier": "ask", "tier_since": TODAY.isoformat(), "policy": "confirm"}, NAVIGATE["id"])
    with pytest.raises(HTTPException) as e:
        graph_review._fold(version(hot), runs, {NAVIGATE["id"]}, fde=True)
    assert e.value.status_code == 409
    # shadow → ask needs no FDE.
    shadow = with_edge({"tier": "shadow", "policy": "confirm"}, NAVIGATE["id"])
    draft, _, changes, proposals = graph_review._fold(version(shadow), runs, {NAVIGATE["id"]})
    assert proposals[0].needs_fde is False and next(e for e in draft["edges"] if e["id"] == NAVIGATE["id"])["tier"] == "ask"
    assert any(c.reason == "promoted by admin" for c in changes)


def test_statistics_alone_never_move_a_tier():
    d = with_edge({"tier": "ask", "tier_since": "2020-01-01", "policy": "confirm"}, NAVIGATE["id"])
    draft, _, changes, proposals = graph_review._fold(version(d), [walk([NAVIGATE]) for _ in range(20)], set())
    assert next(e for e in draft["edges"] if e["id"] == NAVIGATE["id"])["tier"] == "ask"
    assert proposals and not [c for c in changes if c.kind == "tier"]


def test_the_shadow_report_lists_agreement_per_edge_and_every_disagreement_as_a_delta():
    agree = PlannerState(
        recovery={"shadow": [{"edge": TYPE_AMOUNT["id"], "from": BILL_EMPTY["key"], "acted": TYPE_AMOUNT["to"], "agree": True}]}
    )
    differ = PlannerState(
        recovery={
            "shadow": [
                {"edge": TYPE_AMOUNT["id"], "from": BILL_EMPTY["key"], "acted": GRAPH["goal"]["node"], "agree": False},
                {"edge": TYPE_AMOUNT["id"], "from": BILL_EMPTY["key"], "acted": None, "agree": False},
            ]
        }
    )
    runs = [run_with(agree), run_with(differ), walk([NAVIGATE])]
    report = graph_review.shadow_report(runs)
    assert (report.runs, report.proposed, report.agreed, report.agreement) == (2, 3, 1, 0.333)
    (e,) = report.edges
    assert e.edge_id == TYPE_AMOUNT["id"] and e.agreement == 0.333
    assert [(x.frm, x.acted) for x in e.disagreements] == [(BILL_EMPTY["key"], GRAPH["goal"]["node"]), (BILL_EMPTY["key"], None)]
    assert e.disagreements[0].run_id == runs[1].id


def test_review_out_carries_the_shadow_report_shape():
    out = graph_review.GraphReviewOut(
        version_id=uuid.uuid4(),
        version_number=1,
        status="approved",
        graph=None,
        runs=[],
        draft=None,
        changes=[],
        proposals=[],
        shadow=graph_review.shadow_report([]),
        against_previous=[],
        can_draft=False,
    )
    assert out.shadow.agreement is None and out.shadow.edges == []


def test_tier_since_is_a_date():
    assert tiers.cooled({"tier_since": (date.today() - timedelta(days=30)).isoformat()}, date.today())
    assert tiers.cooled({"tier_since": "garbage"}, date.today())
    assert not tiers.cooled({"tier_since": date.today().isoformat()}, date.today())
