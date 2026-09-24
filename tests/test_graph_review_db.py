"""Tier 2 for the admin's graph view: a real approved version, real run rows, the derived draft
over HTTP, and the draft becoming an ordinary version that needs its own approval."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from taskmining.state import PROMOTE_MIN_EXECUTED
from tests.conftest import requires_db
from tests.test_computer_use_db import GRAPH, INPUTS, _drain, approved, company_admin, start
from vista.config import settings
from vista.db import platform_session, tenant_session
from vista.models.platform import User
from vista.models.tenant import WorkflowRun

pytestmark = requires_db

CLICK = next(e for e in GRAPH["graph"]["edges"] if e["action_class"] == "click")
READ = next(e for e in GRAPH["graph"]["edges"] if e["action_class"] == "read")


def graph_path(workflow, version):
    return f"/api/workflows/{workflow['id']}/versions/{version['id']}/graph"


def finished_run(client, headers, schema, workflow, version, *edges, verified=True):
    """A run that reached a terminal state, with the path it (as if) executed written into its checkpoint."""
    run = start(client, headers, workflow, version, inputs={**INPUTS, "input_1": {"kind": "value", "value": "ACME"}}).json()
    _drain()
    assert client.post(f"/api/workflow-runs/{run['id']}/stop", headers=headers, json={"reason": "test"}).status_code == 200
    _drain()
    with tenant_session(schema) as session:
        row = session.get(WorkflowRun, uuid.UUID(run["id"]))
        assert row.status == "stopped"
        row.checkpoint = {
            **row.checkpoint,
            "history": [{"step_id": f"s{i}", "executed": True} for i in range(len(edges))],
            "trajectory": [{"edge": e["id"], "step_id": f"s{i}", "seq": i + 1, "effect_seen": True} for i, e in enumerate(edges)],
        }
        row.outcome = {"verified": verified}
        session.commit()
    return run["id"]


def test_the_graph_view_shows_runs_and_the_admin_turns_them_into_the_next_draft(client, monkeypatch):
    monkeypatch.setattr(settings, "typesafe_api_key", None)
    monkeypatch.delenv("VISTA_JEV_CASSETTE", raising=False)
    headers, company_id, schema, _ = company_admin(client)
    workflow, version = approved(client, headers, GRAPH, name="Recorded")

    empty = client.get(graph_path(workflow, version), headers=headers)
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert body["graph"] == GRAPH["graph"] and body["runs"] == [] and body["draft"] is None and body["can_draft"] is False
    too_early = client.post(f"{graph_path(workflow, version)}/draft", headers=headers, json={"expected_version": 1, "promote": []})
    assert too_early.status_code == 409

    run_ids = [finished_run(client, headers, schema, workflow, version, READ, CLICK) for _ in range(PROMOTE_MIN_EXECUTED["click"])]
    body = client.get(graph_path(workflow, version), headers=headers).json()
    assert [r["run_id"] for r in body["runs"]] == run_ids
    assert [s["edge"] for s in body["runs"][0]["steps"]] == [READ["id"], CLICK["id"]]
    assert body["graph"] == GRAPH["graph"]  # the approved structure is untouched
    stats = {e["id"]: e["stats"] for e in body["draft"]["edges"]}
    assert stats[CLICK["id"]]["executed"] == len(run_ids) and stats[CLICK["id"]]["verified_ok"] == len(run_ids)
    assert [p["edge_id"] for p in body["proposals"]] == [CLICK["id"]] and body["can_draft"] is True
    policy = {e["id"]: e["policy"] for e in body["draft"]["edges"]}
    assert policy[CLICK["id"]] == "confirm"  # proposed, not applied

    stale = client.post(f"{graph_path(workflow, version)}/draft", headers=headers, json={"expected_version": 7, "promote": []})
    assert stale.status_code == 409
    unproposed = client.post(
        f"{graph_path(workflow, version)}/draft", headers=headers, json={"expected_version": 1, "promote": [READ["id"]]}
    )
    assert unproposed.status_code == 422

    created = client.post(f"{graph_path(workflow, version)}/draft", headers=headers, json={"expected_version": 1, "promote": [CLICK["id"]]})
    assert created.status_code == 201, created.text
    draft = created.json()
    assert draft["number"] == 2 and draft["status"] == "draft" and draft["decision"] is None
    new_policy = {e["id"]: e["policy"] for e in draft["definition"]["graph"]["edges"]}
    assert new_policy[CLICK["id"]] == "auto"
    assert draft["definition"]["graph"]["trajectories"] == GRAPH["graph"]["trajectories"]

    review = client.get(graph_path(workflow, draft), headers=headers).json()
    assert review["status"] == "draft" and review["previous_version_number"] == 1
    assert [(c["edge_id"], c["before"], c["after"]) for c in review["against_previous"]] == [(CLICK["id"], "confirm", "auto")]
    assert review["can_draft"] is False

    # v1 is superseded: it can no longer be drafted from, and its runs are not counted twice in v2.
    superseded = client.post(f"{graph_path(workflow, version)}/draft", headers=headers, json={"expected_version": 2, "promote": []})
    assert superseded.status_code == 409
    assert review["runs"] == [] and review["changes"] == []

    decided = client.post(
        f"/api/workflows/{workflow['id']}/versions/{draft['id']}/decision", headers=headers, json={"decision": "approved", "reason": "ok"}
    )
    assert decided.status_code == 200 and decided.json()["status"] == "approved"
    with tenant_session(schema) as session:
        assert session.scalar(select(WorkflowRun.workflow_version_id).where(WorkflowRun.id == uuid.UUID(run_ids[0]))) == uuid.UUID(
            version["id"]
        )


def test_a_version_without_a_graph_still_answers_and_cannot_be_drafted(client, monkeypatch):
    monkeypatch.setattr(settings, "typesafe_api_key", None)
    headers, *_ = company_admin(client)
    workflow, version = approved(client, headers, {k: v for k, v in GRAPH.items() if k != "graph"}, name="Plain")
    body = client.get(graph_path(workflow, version), headers=headers).json()
    assert body["graph"] is None and body["draft"] is None and body["can_draft"] is False
    assert (
        client.post(f"{graph_path(workflow, version)}/draft", headers=headers, json={"expected_version": 1, "promote": []}).status_code
        == 409
    )


def test_a_member_reads_the_graph_but_cannot_draft(client, monkeypatch):
    monkeypatch.setattr(settings, "typesafe_api_key", None)
    headers, company_id, schema, admin_id = company_admin(client)
    workflow, version = approved(client, headers, GRAPH, name="Recorded")
    key = uuid.uuid4().hex + uuid.uuid4().hex
    with platform_session() as session:
        admin = session.get(User, admin_id)
        session.add(User(tenant_id=admin.tenant_id, email=f"{uuid.uuid4().hex}@example.com", api_token=key, role="member"))
        session.commit()
    member = {"Authorization": f"Bearer {key}"}
    assert client.get(graph_path(workflow, version), headers=member).status_code == 200
    assert (
        client.post(f"{graph_path(workflow, version)}/draft", headers=member, json={"expected_version": 1, "promote": []}).status_code
        == 403
    )
