"""The plan graph: a recording compiled on the device into states and moves, validated and
hashed here exactly as the recorder produced it. No database, no model."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from taskmining import state
from vista.automation.schemas import PlanGraph, WorkflowDefinition
from vista.automation.service import definition_hash

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "plan_invoice.json").read_text())
DEFINITION = {
    "goal": "Enter the invoice total into the accounting system",
    "required_inputs": ["input_1"],
    "allowed_tools": ["read_invoice", "create_invoice_draft"],
    "success_criteria": ["The bill total matches the invoice"],
    "environment": "sandbox",
    "limits": {"max_steps": 10, "max_runtime_seconds": 300, "max_cost_usd": "1.00"},
}
SECRETS = ("INV-1042", "ACME", "1,250", "qbo.example", "412", "233", "h-total", "Preview", "QuickBooks")


def with_graph(graph=FIXTURE):
    return {**DEFINITION, "graph": graph}


def test_key_functions_match_the_recorder_byte_for_byte():
    expect = hashlib.sha1(b"accounting|accounting|fact:f1,field:Amount").hexdigest()[:16]
    assert state.state_key("accounting", "accounting", ["field:Amount", "fact:f1"]) == expect
    for n in FIXTURE["nodes"]:
        assert n["key"] == state.state_key(n["app_role"], n["activity"], n["signature"])
    for e in FIXTURE["edges"]:
        assert e["id"] == state.edge_id(e["frm"], e["to"], e["action_class"], e["control"], e["slot"])
    assert state.app_role("QuickBooks", "Bills") == "accounting"
    assert state.app_role("Preview", "INV.pdf") == "pdf"
    assert state.app_role("Something") == "other"


def test_the_recorder_fixture_is_a_valid_privacy_preserving_graph():
    graph = PlanGraph.model_validate(FIXTURE)
    text = graph.model_dump_json()
    for s in SECRETS:
        assert s not in text
    assert {n.app_role for n in graph.nodes} == {"pdf", "accounting"}
    assert graph.slots == {"f1", "input_1"}
    assert graph.produced == {"f1"}
    submit = next(e for e in graph.edges if e.action_class == "submit")
    assert submit.policy == "always_ask"
    assert all(e.provenance[0].id == "rec-invoice-1" and e.provenance[0].event_ids for e in graph.edges)
    # JSON round trip is the identity: what the device signed is what the version stores.
    assert graph.model_dump(mode="json", exclude_none=True) == {
        **FIXTURE,
        "edges": [{k: v for k, v in e.items() if v is not None} for e in FIXTURE["edges"]],
    }


def test_graph_is_part_of_the_immutable_definition_and_old_definitions_keep_their_shape():
    plain = WorkflowDefinition.model_validate(DEFINITION).model_dump(mode="json")
    assert "graph" not in plain
    assert definition_hash(plain) == definition_hash(DEFINITION)
    full = WorkflowDefinition.model_validate(with_graph()).model_dump(mode="json")
    assert full["graph"]["nodes"]
    h1 = definition_hash(full)
    changed = copy.deepcopy(with_graph())
    changed["graph"]["edges"][0]["stats"]["support"] += 1
    assert definition_hash(WorkflowDefinition.model_validate(changed).model_dump(mode="json")) != h1
    reordered = copy.deepcopy(with_graph())
    reordered["graph"]["edges"].reverse()
    assert definition_hash(WorkflowDefinition.model_validate(reordered).model_dump(mode="json")) != h1


def broken(mutate):
    graph = copy.deepcopy(FIXTURE)
    mutate(graph)
    return graph


def set_first_edge(field, value):
    def mutate(g):
        g["edges"][0][field] = value

    return mutate


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda g: g["nodes"][0].__setitem__("key", "0" * 16), "key"),
        (lambda g: g["nodes"][0]["signature"].append("title:INV-1042 ACME"), "pattern"),
        (lambda g: g["nodes"][0]["signature"].append("url:https"), "pattern"),
        (set_first_edge("id", "f" * 16), "id"),
        (set_first_edge("action_class", "shell"), "action_class"),
        (set_first_edge("frm", "1" * 16), "id"),
        (set_first_edge("provenance", []), "provenance"),
        (lambda g: [e.update(policy="auto") for e in g["edges"] if e["action_class"] == "submit"], "submit"),
        (lambda g: g["start"].append("2" * 16), "reference"),
        (lambda g: g["edges"].append(dict(g["edges"][0])), "Duplicate"),
        (lambda g: g.__setitem__("compiled_by", "chatgpt"), "compiled_by"),
        (lambda g: g.__setitem__("trajectories", 0), "trajectories"),
    ],
)
def test_graphs_that_are_not_what_the_recorder_compiles_are_rejected(mutate, message):
    with pytest.raises(ValidationError) as err:
        PlanGraph.model_validate(broken(mutate))
    assert message.lower() in str(err.value).lower()


def test_graph_size_is_bounded():
    big = copy.deepcopy(FIXTURE)
    for i in range(201):
        sig = [f"fact:x{i}"]
        big["nodes"].append({"key": state.state_key("other", "other", sig), "app_role": "other", "activity": "other", "signature": sig})
    with pytest.raises(ValidationError):
        PlanGraph.model_validate(big)


def test_graph_slots_must_be_declared_inputs_or_produced_facts():
    undeclared = {**DEFINITION, "required_inputs": ["invoice"], "graph": FIXTURE}
    with pytest.raises(ValidationError) as err:
        WorkflowDefinition.model_validate(undeclared)
    assert "input_1" in str(err.value) and "f1" not in str(err.value)


def test_merge_is_order_independent_and_sums_support():
    second = copy.deepcopy(FIXTURE)
    for e in second["edges"]:
        e["provenance"] = [{"source": "recording", "id": "rec-invoice-2", "event_ids": ["e1"]}]
        e["anchor_ref"] = f"rec-invoice-2:{e['id']}"
    second["edges"] = [e for e in second["edges"] if e["action_class"] != "click"]
    a = state.merge_graphs([FIXTURE, second])
    b = state.merge_graphs([second, FIXTURE])
    assert a == b
    assert a["trajectories"] == 2
    read = next(e for e in a["edges"] if e["action_class"] == "read")
    assert read["stats"]["support"] == 2 and read["stats"]["recorded"] == 2
    assert [p["id"] for p in read["provenance"]] == ["rec-invoice-1", "rec-invoice-2"]
    click = next(e for e in a["edges"] if e["action_class"] == "click")
    assert click["stats"]["support"] == 1
    PlanGraph.model_validate(a)
    assert definition_hash(a) == definition_hash(b)


def test_promotion_is_per_edge_code_only_and_never_for_submit():
    def edge(action, **stats):
        return {"action_class": action, "stats": {**state.empty_stats(), **stats}}

    assert state.promotable(edge("read", executed=3, verified_ok=3))
    assert not state.promotable(edge("read", executed=2, verified_ok=2))
    assert not state.promotable(edge("click", executed=4, verified_ok=4))
    assert state.promotable(edge("click", executed=5, verified_ok=5))
    assert not state.promotable(edge("click", executed=10, verified_ok=8))
    assert not state.promotable(edge("click", executed=10, verified_ok=10, denied=1))
    assert not state.promotable(edge("click", executed=10, verified_ok=10, effect_missing=2))
    assert not state.promotable(edge("submit", executed=100, verified_ok=100))
    assert state.default_policy("submit") == "always_ask"
    assert state.stricter("auto", "confirm") == "confirm"
    assert state.stricter("always_ask", "auto") == "always_ask"
