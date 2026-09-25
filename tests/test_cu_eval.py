"""Step 9 harness: recording-compiled graphs only, synthetic stays smoke, one revision per report,
frozen test set, milestone counters."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from taskmining import evaluate

FIXTURES = Path(__file__).parent / "fixtures"
GRAPH = json.loads((FIXTURES / "plan_invoice_v3.json").read_text())


def case(**over) -> dict:
    c = {"id": "c1", "kind": "recording", "apps": ["crm"], "people": ["p1"], "tasks": ["t1"], "graph": GRAPH, "held_out": [], "runs": []}
    c.update(over)
    return c


def test_dev_set_scores_the_synthetic_fixture_as_smoke_only():
    s = evaluate.load_set(FIXTURES / "cu" / "dev")
    report = evaluate.evaluate(s, "abc", False)
    assert report["benchmark"]["held_out"] == 0 and report["benchmark"]["locate_accuracy"] is None
    assert report["smoke"]["located_correct"] == report["smoke"]["held_out"] == len(GRAPH["nodes"]) + 1
    assert report["smoke"]["leakage_failures"] == 0
    assert report["milestones"]["milestone_1"]["met"] is False
    assert "not a claim" in evaluate.markdown(report)


def test_locate_accuracy_counts_exact_node_or_correct_unknown():
    n = GRAPH["nodes"][0]
    held = [
        {"l0": n["l0"], "expected": n["key"], "recording": "rec-9"},
        {"l0": n["l0"], "expected": GRAPH["nodes"][1]["key"], "recording": "rec-9"},
        {"l0": ["in:ffffffffffffffff"], "expected": None, "recording": "rec-9"},
        {"l0": ["in:ffffffffffffffff"], "expected": n["key"], "recording": "rec-9"},
    ]
    m = evaluate.score_case(case(held_out=held), GRAPH)
    assert (m.held_out, m.located_correct, m.located_any) == (4, 2, 2)
    assert m.ratios()["locate_accuracy"] == 0.5


def test_held_out_frames_may_not_come_from_a_compile_recording():
    with pytest.raises(evaluate.EvalError, match="compiled from"):
        evaluate.score_case(case(held_out=[{"l0": [], "expected": None, "recording": "rec-1"}]), GRAPH)


def test_runs_contribute_statistics_only():
    e = GRAPH["edges"][0]["id"]
    runs = [
        {"run_id": "r1", "verified": True, "rejudged": 1, "recovery_used": 2, "leakage_failed": 1, "edges": [e, "nope"]},
        {"run_id": "r2", "verified": False, "edges": [e]},
        {"run_id": "r3", "verified": None},
    ]
    m = evaluate.score_case(case(runs=runs), GRAPH)
    assert (m.runs, m.runs_with_verdict, m.verified_ok) == (3, 2, 1)
    assert (m.rejudged, m.recovery_used, m.leakage_failures) == (1, 2, 1)
    assert m.ratios()["move_coverage"] == round(2 / 3, 4)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda g: g.update(compiled_by="hand"), "recorder-plan"),
        (lambda g: g["edges"][0]["provenance"].append({"source": "llm", "id": "x", "event_ids": []}), "not a recording or a run"),
        (lambda g: g["edges"][0].__setitem__("provenance", []), "no provenance"),
        (lambda g: g["nodes"][0].pop("l0"), "l0"),
    ],
)
def test_only_recording_compiled_graphs_are_evaluated(mutate, message):
    g = copy.deepcopy(GRAPH)
    mutate(g)
    with pytest.raises(evaluate.EvalError, match=message):
        evaluate.score_case(case(), g)


def test_combine_refuses_other_revisions_and_dirty_trees():
    s = evaluate.load_set(FIXTURES / "cu" / "dev")
    a = evaluate.evaluate(s, "aaa", False)
    b = evaluate.evaluate(s, "bbb", False)
    with pytest.raises(evaluate.EvalError, match="different revisions"):
        evaluate.combine([a, b])
    with pytest.raises(evaluate.EvalError, match="dirty"):
        evaluate.combine([evaluate.evaluate(s, "aaa", True)])
    both = evaluate.combine([a, evaluate.evaluate(s, "aaa", False)])
    assert both["smoke"]["held_out"] == 2 * a["smoke"]["held_out"]


def test_frozen_set_refuses_changed_cases(tmp_path):
    root = tmp_path / "test"
    root.mkdir()
    (root / "g.json").write_text(json.dumps(GRAPH))
    (root / "c.json").write_text(json.dumps(case(graph="g.json")))
    (root / "set.json").write_text(json.dumps({"name": "test", "cases": ["c.json"]}))
    evaluate.freeze(root, "2026-09-25")
    assert evaluate.load_set(root).frozen == "2026-09-25"
    (root / "c.json").write_text(json.dumps(case(graph="g.json", apps=["other"])))
    with pytest.raises(evaluate.EvalError, match="frozen"):
        evaluate.load_set(root)


def test_committed_test_set_is_frozen_and_loads():
    s = evaluate.load_set(FIXTURES / "cu" / "test")
    assert s.frozen


def test_milestones_count_real_recordings_only():
    s = evaluate.EvalSet(name="x", root=Path("."), frozen=None)
    g_nav = copy.deepcopy(GRAPH)
    for e in g_nav["edges"]:
        e["irreversibility"] = "navigational"
    assert any(e["irreversibility"] != "navigational" for e in GRAPH["edges"])
    for i, (kind, apps, people, g) in enumerate(
        [
            ("recording", ["crm"], ["ann"], g_nav),
            ("recording", ["erp"], ["bob"], GRAPH),
            ("synthetic", ["fake"], ["nobody"], GRAPH),
        ]
    ):
        c = case(
            id=f"c{i}",
            kind=kind,
            apps=apps,
            people=people,
            tasks=[f"t{i}"],
            held_out=[{"l0": [], "expected": None, "recording": f"rec-h{i}"}],
        )
        s.cases.append(c)
        s.graphs[c["id"]] = g
    ms = evaluate.milestones(s)
    assert ms["have"] == {"recordings": 3, "tasks": 2, "apps": 2, "people": 2}
    assert ms["milestone_1"] == {"met": False, "unmet": {"tasks": 5}}
    assert ms["milestone_2"] == {"met": False, "apps_without_write_task": ["crm"]}
