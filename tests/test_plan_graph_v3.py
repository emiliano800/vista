"""Task graph v3: L0 frames compiled on the device (recorder-plan/3), validated and merged here
with the same key functions. Fixture: tests/fixtures/plan_invoice_v3.json (node test/plan-fixture.mjs)."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from taskmining import state
from vista.automation.schemas import PlanGraph, WorkflowDefinition

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "plan_invoice_v3.json").read_text())
SECRETS = ("INV-1042", "ACME", "1,250", "qbo.example", "h-total", "Total", "Memo", "Beta", "Bills", "Preview", "QuickBooks")
DEFINITION = {
    "goal": "Enter the invoice total into the accounting system",
    "required_inputs": ["input_1"],
    "allowed_tools": ["read_invoice"],
    "success_criteria": ["The bill total matches the invoice"],
}


def test_frame_keys_match_the_recorder_byte_for_byte():
    assert (
        state.frame_key(["read:fact:f1", "have:field:amount", "in:abc"])
        == hashlib.sha1(b"v3|have:field:amount,in:abc,read:fact:f1").hexdigest()[:16]
    )
    assert state.frame_key(["a", "b"]) == state.frame_key(["b", "a", "a"])
    assert FIXTURE["compiled_by"] == "recorder-plan/3"
    for n in FIXTURE["nodes"]:
        assert n["key"] == state.frame_key(n["l0"]) == state.node_key(n)
        assert n["signature"] == n["l0"]
        assert any(x.startswith("in:") for x in n["l0"])
    for e in FIXTURE["edges"]:
        assert e["id"] == state.edge_id(e["frm"], e["to"], e["action_class"], e["control"], e["slot"])
        assert e["irreversibility"] in state.IRREVERSIBILITY
        assert e["policy"] == ("always_ask" if e["irreversibility"] == "committing" else e["policy"])


def test_the_v3_fixture_validates_and_carries_no_values():
    graph = PlanGraph.model_validate(FIXTURE)
    text = graph.model_dump_json()
    for s in SECRETS:
        assert s not in text
    for key in ("x", "y", "url", "window_title", "selector", "points"):
        assert f'"{key}"' not in text
    assert graph.goal is not None and graph.goal.node in {n.key for n in graph.nodes}
    assert [c.type for c in graph.goal.criteria] == ["present"] * 3
    assert {s.slot: s.method for s in graph.slot_table} == {"fact:f1": "transfer", "input_1": "declared"}
    assert graph.slots == {"fact:f1", "input_1"} and graph.produced == {"fact:f1"}
    WorkflowDefinition.model_validate({**DEFINITION, "graph": FIXTURE})
    typed = next(e for e in graph.edges if e.slot == "input_1")
    assert typed.commit == "enter" and typed.irreversibility == "mutating"
    dumped = graph.model_dump(mode="json")
    assert PlanGraph.model_validate(dumped).model_dump(mode="json") == dumped
    assert [n["key"] for n in dumped["nodes"]] == [n["key"] for n in FIXTURE["nodes"]]
    for got, want in zip(dumped["edges"], FIXTURE["edges"], strict=True):
        assert {k: v for k, v in want.items() if v is not None and k != "descriptor"}.items() <= got.items()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda g: g["nodes"][0]["l0"].append("ctx:confirm"), "Node key does not match"),
        (lambda g: g["nodes"][0]["signature"].append("have:field:x"), "must mirror its L0"),
        (lambda g: g["nodes"][0].pop("l0"), "Node key does not match"),
        (
            lambda g: [n.update(key=state.state_key(n["app_role"], n["activity"], n["signature"])) or n.pop("l0") for n in g["nodes"]],
            "v3 graph's nodes carry L0",
        ),
        (lambda g: g["nodes"][0]["l0"].append("in:not-a-hash"), "String should match pattern"),
        (lambda g: g["goal"].__setitem__("node", "0" * 16), "goal frame must be a node"),
        (
            lambda g: next(e for e in g["edges"] if e["action_class"] == "submit").__setitem__("irreversibility", "mutating"),
            "submit edge is committing",
        ),
        (
            lambda g: next(e for e in g["edges"] if e["irreversibility"] == "committing").__setitem__("policy", "confirm"),
            "always asks a person",
        ),
        (lambda g: g["slot_table"][0].__setitem__("method", "guessed"), "Input should be"),
        (lambda g: g["goal"]["criteria"][0].__setitem__("type", "vibes"), "Input should be"),
    ],
)
def test_v3_graphs_that_are_not_what_the_recorder_compiles_are_rejected(mutate, message):
    g = copy.deepcopy(FIXTURE)
    mutate(g)
    with pytest.raises(ValidationError, match=message):
        PlanGraph.model_validate(g)


def test_irreversibility_is_assigned_in_code_and_only_ever_raised():
    assert state.irreversibility_of("navigate") == "navigational"
    assert state.irreversibility_of("click", {"role": "link", "name": "deals"}) == "navigational"
    assert state.irreversibility_of("click", {"role": "button", "name": "add line"}) == "mutating"
    assert state.irreversibility_of("click", {"role": "button", "name": "save"}) == "committing"
    assert state.irreversibility_of("press", {"role": "key", "name": "enter"}, ctx="confirm") == "committing"
    assert state.irreversibility_of("press", {"role": "key", "name": "enter"}) == "mutating"
    assert state.irreversibility_of("type_value", {"role": "textbox", "name": "amount"}) == "mutating"
    assert state.irreversibility_of("submit") == "committing"
    assert state.raise_irreversibility("mutating", "committing") == "committing"
    assert state.raise_irreversibility("committing", "navigational") == "committing"
    assert state.raise_irreversibility("mutating", None) == "mutating"
    assert state.default_policy("click", "navigational") == "auto"
    assert state.default_policy("click", "committing") == "always_ask"
    assert state.default_policy("type_value", "mutating") == "confirm"


def test_locate_is_l0_equality_and_merge_keeps_slots_goal_and_stricter_class():
    a, b = FIXTURE["nodes"][0], FIXTURE["nodes"][1]
    assert state.same_frame(a, {"l0": list(reversed(a["l0"]))})
    assert not state.same_frame(a, b)
    other = copy.deepcopy(FIXTURE)
    for e in other["edges"]:
        e["provenance"] = [{"source": "recording", "id": "rec-2", "event_ids": ["e1"]}]
    click = next(e for e in other["edges"] if e["action_class"] == "click")
    click["irreversibility"] = "committing"
    click["policy"] = "always_ask"
    other["slot_table"][1]["single_recording"] = True
    merged = state.merge_graphs([FIXTURE, other])
    assert merged == state.merge_graphs([other, FIXTURE])
    assert merged["trajectories"] == 2 and merged["goal"] == FIXTURE["goal"]
    assert next(s for s in merged["slot_table"] if s["slot"] == "input_1")["single_recording"] is False
    m_click = next(e for e in merged["edges"] if e["action_class"] == "click")
    assert m_click["irreversibility"] == "committing" and m_click["policy"] == "always_ask" and m_click["stats"]["support"] == 2
    PlanGraph.model_validate(merged)


def test_a_type_value_edge_carries_a_bounded_time_ordered_key_script_outside_its_identity():
    typed = next(e for e in FIXTURE["edges"] if e["action_class"] == "type_value")
    script = [{"t": 0, "key": "A"}, {"t": 82, "key": "C"}, {"t": 151, "key": "M"}, {"t": 900, "key": "Enter"}]
    script.append({"t": 950, "key": "•", "masked": True})
    with_keys = copy.deepcopy(FIXTURE)
    edge = next(e for e in with_keys["edges"] if e["id"] == typed["id"])
    edge["keys"] = script
    graph = PlanGraph.model_validate(with_keys)
    dumped = graph.model_dump(mode="json")
    got = next(e for e in dumped["edges"] if e["id"] == typed["id"])
    assert got["keys"] == script, "unmasked keys serialise without `masked`; masked ones keep it"
    assert all("keys" not in e for e in dumped["edges"] if e["id"] != typed["id"]), "absent scripts do not serialise"
    assert got["id"] == typed["id"], "keys are not identity"
    assert PlanGraph.model_validate(FIXTURE).model_dump(mode="json")["nodes"] == dumped["nodes"]
    for bad in (
        [{"t": 0, "key": ""}],
        [{"t": -1, "key": "A"}],
        [{"t": "0", "key": "A"}],
        [{"t": 100, "key": "A"}, {"t": 50, "key": "B"}],
        [{"t": 0, "key": "x" * 33}],
        [{"t": i, "key": "a"} for i in range(2001)],
    ):
        edge["keys"] = bad
        with pytest.raises(ValidationError):
            PlanGraph.model_validate(with_keys)
    edge["keys"] = script
    other = next(e for e in with_keys["edges"] if e["action_class"] == "click")
    other["keys"] = script
    with pytest.raises(ValidationError, match="type_value"):
        PlanGraph.model_validate(with_keys)
