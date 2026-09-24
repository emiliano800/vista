"""The admin's view of a version's task graph: runs fold into a derived draft, promotions are
proposed by code and chosen by a person, demotions are automatic. Pure logic here (no DB); the
fixture graph and definition come from `test_computer_use_graph`."""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from taskmining.state import PROMOTE_MIN_EXECUTED
from tests.test_computer_use_graph import DEFINITION, edge
from vista.automation import graph_review
from vista.automation.schemas import PlanGraph
from vista.computer_use.planner import PlannerState


def run(state: PlannerState, *, verified: bool | None = True, status: str = "succeeded") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=status,
        mode="sandbox",
        outcome=None if verified is None else {"verified": verified},
        finished_at=datetime.now(UTC),
        checkpoint=state.to_checkpoint(),
    )


def version(definition: dict, number: int = 1) -> SimpleNamespace:
    return SimpleNamespace(definition=definition, number=number)


def walk(*actions: str, verified: bool = True, effect_seen: bool = True, deny: str | None = None) -> SimpleNamespace:
    edges = [edge(a) for a in actions]
    state = PlannerState(
        n=len(edges),
        history=[{"step_id": f"s{i}", "executed": True} for i in range(len(edges))],
        trajectory=[{"edge": e["id"], "step_id": f"s{i}", "seq": i + 1, "effect_seen": effect_seen} for i, e in enumerate(edges)],
    )
    if deny:
        state.proposals = [{"edge": edge(deny)["id"], "step_id": "p0"}]
        state.decisions = {"p0": {"decision": "deny"}}
    return run(state, verified=verified)


def test_a_version_without_runs_has_no_draft_and_nothing_to_approve():
    draft, views, changes, proposals = graph_review._fold(version(DEFINITION), [], set())
    assert draft is not None and views == [] and changes == [] and proposals == []
    assert draft["edges"] == DEFINITION["graph"]["edges"]  # folding nothing changes nothing


def test_runs_show_their_path_and_add_statistics_without_touching_the_approved_graph():
    before = copy.deepcopy(DEFINITION)
    r = walk("read", "navigate", deny="submit")
    draft, views, changes, proposals = graph_review._fold(version(DEFINITION), [r], set())
    assert DEFINITION == before
    PlanGraph.model_validate(draft)
    assert draft["trajectories"] == DEFINITION["graph"]["trajectories"]  # runs are not recorded trajectories
    (view,) = views
    assert view.run_id == r.id and view.verified is True
    assert [(s.edge, s.executed, s.decision) for s in view.steps] == [
        (edge("read")["id"], True, None),
        (edge("navigate")["id"], True, None),
        (edge("submit")["id"], False, "deny"),
    ]
    by_id = {c.edge_id: c for c in changes}
    assert {c.kind for c in changes} == {"stats"}
    assert by_id[edge("read")["id"]].after["executed"] == by_id[edge("read")["id"]].before["executed"] + 1
    assert by_id[edge("submit")["id"]].after["denied"] == 1
    assert proposals == []  # one run never clears a promotion threshold


def test_code_proposes_promotion_and_only_the_admin_s_choice_applies_it():
    need = PROMOTE_MIN_EXECUTED["click"]
    runs = [walk("click") for _ in range(need)]
    click = edge("click")
    assert click["policy"] == "confirm"
    draft, _, changes, proposals = graph_review._fold(version(DEFINITION), runs, set())
    assert [p.edge_id for p in proposals] == [click["id"]] and proposals[0].to_policy == "auto"
    assert next(e for e in draft["edges"] if e["id"] == click["id"])["policy"] == "confirm"  # proposed, not applied
    assert not any(c.kind == "policy" for c in changes)

    draft, _, changes, _ = graph_review._fold(version(DEFINITION), runs, {click["id"]})
    assert next(e for e in draft["edges"] if e["id"] == click["id"])["policy"] == "auto"
    assert [(c.before, c.after) for c in changes if c.kind == "policy"] == [("confirm", "auto")]
    PlanGraph.model_validate(draft)


def test_submit_is_never_proposed_however_often_it_verifies():
    runs = [walk("submit") for _ in range(10)]
    _, _, _, proposals = graph_review._fold(version(DEFINITION), runs, set())
    assert proposals == []


def test_demotion_is_automatic_on_a_denial_or_a_missing_effect():
    definition = copy.deepcopy(DEFINITION)
    nav = next(e for e in definition["graph"]["edges"] if e["id"] == edge("navigate")["id"])
    nav["policy"] = "auto"
    denied = walk("read", deny="navigate")
    draft, _, changes, _ = graph_review._fold(version(definition), [denied], set())
    assert next(e for e in draft["edges"] if e["id"] == nav["id"])["policy"] == "confirm"
    policy = [c for c in changes if c.kind == "policy"]
    assert [(c.before, c.after, c.reason) for c in policy] == [("auto", "confirm", "denied > 0")]

    missing = walk("navigate", effect_seen=False)
    draft, _, changes, _ = graph_review._fold(version(definition), [missing], set())
    assert next(e for e in draft["edges"] if e["id"] == nav["id"])["policy"] == "confirm"
    assert [c.reason for c in changes if c.kind == "policy"] == ["effect_missing > 0"]


def test_an_unverified_or_stopped_run_counts_executions_but_not_successes():
    r = walk("read", verified=False)
    _, views, changes, _ = graph_review._fold(version(DEFINITION), [r], set())
    assert views[0].verified is False
    change = next(c for c in changes if c.edge_id == edge("read")["id"])
    assert change.after["executed"] == change.before["executed"] + 1
    assert change.after["verified_ok"] == change.before["verified_ok"]
    stopped = walk("read", verified=None)
    _, views, _, _ = graph_review._fold(version(DEFINITION), [stopped], set())
    assert views[0].verified is None


def test_the_diff_against_the_previous_approved_graph_lists_only_policy_changes_and_new_edges():
    prev = DEFINITION["graph"]
    cur = copy.deepcopy(prev)
    cur["edges"][0]["policy"] = "auto" if cur["edges"][0]["policy"] != "auto" else "confirm"
    extra = copy.deepcopy(cur["edges"][1])
    extra["id"] = "f" * 16
    cur["edges"].append(extra)
    diff = graph_review._structural_diff(prev, cur)
    assert [(c.edge_id, c.before, c.after, c.reason) for c in diff] == [
        (cur["edges"][0]["id"], prev["edges"][0]["policy"], cur["edges"][0]["policy"], ""),
        ("f" * 16, "", extra["policy"], "new edge"),
    ]
    assert graph_review._structural_diff(None, cur) == [] and graph_review._structural_diff(prev, prev) == []


def test_a_definition_without_a_graph_has_runs_but_no_draft():
    definition = {k: v for k, v in DEFINITION.items() if k != "graph"}
    draft, views, changes, proposals = graph_review._fold(version(definition), [walk("read")], set())
    assert draft is None and len(views) == 1 and changes == [] and proposals == []


@pytest.mark.parametrize("policy", ["confirm", "always_ask"])
def test_only_auto_edges_are_demoted(policy):
    assert graph_review.demotion_reason({"policy": policy, "stats": {"denied": 3, "effect_missing": 0}}) is None
    assert graph_review.demotion_reason({"policy": "auto", "stats": {"denied": 0, "effect_missing": 0}}) is None
