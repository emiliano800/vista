"""Tier 2 for the Computer Use Agent: real queue, worker, tenant ledger and the recorder
protocol, with Jev scripted or stubbed. Needs Postgres (`docker compose up -d`)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from tests.conftest import requires_db
from tests.test_computer_use import ScriptedJudge
from tests.test_portfolio import make_firm
from vista.agents.jev import NONE
from vista.config import settings
from vista.db import platform_session, tenant_session
from vista.jobs.worker import process_one
from vista.models.platform import FirmCompany, Job, Tenant, User
from vista.models.tenant import AgentRun, AgentRunEvent, Finding, HarnessStep, Task, UsageEvent, WorkflowRun

pytestmark = requires_db

DOCS_ONLY = {
    "goal": "Read the supplier invoice export and stop.",
    "required_inputs": ["Sandbox form URL"],
    "allowed_tools": ["read_source_records", "map_fields"],
    "success_criteria": ["The export was read"],
    "environment": "sandbox",
    "limits": {"max_steps": 6, "max_runtime_seconds": 300, "max_cost_usd": "1.00"},
}
BROWSER = {
    **DOCS_ONLY,
    "goal": "Enter the invoice into the sandbox form and submit it.",
    "allowed_tools": ["read_source_records", "write_destination_records", "compare_with_manual_entry"],
    "success_criteria": ["The form shows the saved invoice"],
}
INPUTS = {"Sandbox form URL": {"kind": "value", "value": "http://localhost:8765/_sandbox/entry.html"}}
DEVICE = {"device_id": "device-1", "platform": "darwin", "browser": "true", "desktop": "true", "recorder_version": "0.3.0"}


def _drain(max_jobs: int = 20) -> None:
    for _ in range(max_jobs):
        if not process_one():
            return


def company_admin(client):
    """A tenant `admin` inside a portfolio company's own tenant — the workspace principal."""
    firm_headers, firm_id = make_firm(role="admin")
    response = client.post("/api/portfolio/companies", headers=firm_headers, json={"name": f"CU Company {uuid.uuid4().hex[:6]}"})
    assert response.status_code == 201, response.text
    company_id = response.json()["id"]
    key = uuid.uuid4().hex + uuid.uuid4().hex
    with platform_session() as session:
        company = session.get(FirmCompany, uuid.UUID(company_id))
        user = User(tenant_id=company.tenant_id, email=f"{uuid.uuid4().hex}@example.com", api_token=key, role="admin")
        session.add(user)
        session.flush()
        schema = session.scalar(select(Tenant.schema_name).where(Tenant.id == company.tenant_id))
        user_id = user.id
        session.commit()
    return {"Authorization": f"Bearer {key}"}, company_id, schema, user_id


def approved(client, headers, definition, name="Sandbox entry"):
    workflow = client.post("/api/workflows", headers=headers, json={"name": name, "definition": definition}).json()
    version = workflow["latest_version"]
    decision = client.post(
        f"/api/workflows/{workflow['id']}/versions/{version['id']}/decision",
        headers=headers,
        json={"decision": "approved", "reason": "test"},
    )
    assert decision.status_code == 200, decision.text
    return workflow, version


def eligibility(client, headers, workflow, version):
    return client.get(f"/api/workflows/{workflow['id']}/versions/{version['id']}/eligibility", headers=headers).json()


def start(client, headers, workflow, version, **body):
    return client.post(f"/api/workflows/{workflow['id']}/versions/{version['id']}/runs", headers=headers, json={"inputs": INPUTS, **body})


def events(schema, agent_run_id):
    with tenant_session(schema) as session:
        rows = session.scalars(
            select(AgentRunEvent).where(AgentRunEvent.run_id == uuid.UUID(agent_run_id)).order_by(AgentRunEvent.seq)
        ).all()
        return [(e.event_type, dict(e.data)) for e in rows]


def test_tenant_admin_can_approve_and_the_stub_model_executes_nothing(client, monkeypatch):
    monkeypatch.setattr(settings, "typesafe_api_key", None)
    monkeypatch.delenv("VISTA_JEV_CASSETTE", raising=False)
    headers, company_id, schema, _ = company_admin(client)
    workflow, version = approved(client, headers, DOCS_ONLY)
    gate = eligibility(client, headers, workflow, version)
    assert gate["eligible"] is True and gate["execution_available"] is True and gate["availability"]["harnesses"] == {"documents": True}

    created = start(client, headers, workflow, version)
    assert created.status_code == 201, created.text
    run = created.json()
    assert run["status"] == "queued" and run["mode"] == "sandbox" and run["version_number"] == 1
    _drain()
    run = client.get(f"/api/workflow-runs/{run['id']}", headers=headers).json()
    assert run["status"] == "waiting_for_human" and run["pending"]["reason"] == "no_action" and run["steps_used"] == 1
    kinds = [t for t, _ in events(schema, run["agent_run_id"])]
    assert kinds[0] == "step" and "handoff" in kinds and "result" not in kinds
    executed = [d for t, d in events(schema, run["agent_run_id"]) if t == "tool_call" and d.get("executed") and d.get("tool") != "observe"]
    assert [d["tool"] for d in executed] == ["wait"]  # the stub's blank answer may only wait; nothing was read or written
    with tenant_session(schema) as session:
        assert session.scalar(select(Task).where(Task.source_type == "agent_run")).title.startswith("Decide step")
        assert session.scalar(select(UsageEvent.model).where(UsageEvent.run_id == uuid.UUID(run["agent_run_id"]))) == "stub-jev-v0"

    stopped = client.post(f"/api/workflow-runs/{run['id']}/stop", headers=headers, json={"reason": "enough"})
    assert stopped.status_code == 200
    _drain()
    run = client.get(f"/api/workflow-runs/{run['id']}", headers=headers).json()
    assert run["status"] == "stopped" and events(schema, run["agent_run_id"])[-1][0] == "result"
    analytics = client.get("/api/agents/analytics", headers=headers).json()
    assert any(a["agent_key"] == "computer_use" for a in analytics["agents"])


def test_documents_run_finishes_with_verification_finding_and_usage(client, monkeypatch):
    judge = ScriptedJudge({"action": "done"}, {"goal_met": 0.9, "criterion_0": 0.95})
    monkeypatch.setattr("vista.computer_use.handler.judge", judge)
    headers, company_id, schema, _ = company_admin(client)
    workflow, version = approved(client, headers, DOCS_ONLY)
    run = start(client, headers, workflow, version).json()
    _drain()
    run = client.get(f"/api/workflow-runs/{run['id']}", headers=headers).json()
    assert run["status"] == "succeeded" and run["outcome"]["verified"] is True and run["outcome"]["matched"] == 1
    kinds = [t for t, _ in events(schema, run["agent_run_id"])]
    assert kinds[0] == "step" and kinds[-1] == "result" and kinds.count("model_call") == 2 and "finding" in kinds
    with tenant_session(schema) as session:
        finding = session.scalar(select(Finding).where(Finding.run_id == uuid.UUID(run["agent_run_id"])))
        assert finding.kind == "observed_fact" and finding.finding_type == "workflow.execution"
        assert f"workflow_run:{run['id']}" in finding.evidence["refs"] and finding.evidence["verification"]["verified"] is True
        assert session.scalar(select(UsageEvent.run_id).where(UsageEvent.run_id == uuid.UUID(run["agent_run_id"]))) is not None
        header = session.get(WorkflowRun, uuid.UUID(run["id"]))
        assert header.lease_until is None and header.definition_hash == version["definition_hash"]
    listed = client.get(f"/api/workflows/{workflow['id']}/runs", headers=headers).json()
    assert [r["id"] for r in listed] == [run["id"]]


def test_browser_run_needs_a_connected_consenting_recorder_and_pauses_before_submit(client, monkeypatch):
    submit = {"action": "submit", "target": "button: Submit invoice", "irreversible": 0.9}
    judge = ScriptedJudge(submit, submit, {"action": "done"}, {"goal_met": 0.85, "criterion_0": 0.9})
    monkeypatch.setattr("vista.computer_use.handler.judge", judge)
    headers, company_id, schema, user_id = company_admin(client)
    workflow, version = approved(client, headers, BROWSER)

    gate = eligibility(client, headers, workflow, version)
    assert gate["eligible"] is True and gate["execution_available"] is False
    assert gate["availability"]["reasons"] == ["harness_not_connected"]
    assert start(client, headers, workflow, version).status_code == 409

    presence = client.get("/api/recorder/computer-use/sessions", headers=headers, params=DEVICE)
    assert presence.status_code == 200 and presence.json()["offers"] == [] and presence.json()["device"]["capabilities"]["browser"] is True
    gate = eligibility(client, headers, workflow, version)
    assert gate["execution_available"] is True and gate["availability"]["device"]["device_id"] == "device-1"

    run = start(client, headers, workflow, version).json()
    assert run["status"] == "waiting_for_harness" and run["pending"]["kind"] == "offer" and run["pending"]["harness_kinds"] == ["browser"]
    assert process_one() is False  # nothing is queued until an employee consents
    offers = client.get("/api/recorder/computer-use/sessions", headers=headers, params=DEVICE).json()["offers"]
    assert [o["run_id"] for o in offers] == [run["id"]] and offers[0]["workflow"]["name"] == "Sandbox entry"

    consent = {"version": "computer-use-v1", "accepted_at": "2026-09-22T10:00:00Z", "screenshots": False}
    claim = client.post(
        f"/api/recorder/computer-use/sessions/{run['id']}/claim",
        headers=headers,
        json={"device_id": "device-1", "consent": consent, "capabilities": {"browser": True, "desktop": True}},
    )
    assert claim.status_code == 200, claim.text
    session_id, token = claim.json()["id"], claim.json()["lease"]["token"]
    assert (
        client.post(
            f"/api/recorder/computer-use/sessions/{run['id']}/claim",
            headers=headers,
            json={"device_id": "device-2", "consent": consent, "capabilities": {"browser": True}},
        ).status_code
        == 409
    )

    # The worker files an observe step and waits.
    _drain()
    polled = client.get(
        f"/api/recorder/computer-use/sessions/{session_id}", headers=headers, params={"device_id": "device-1", "lease_token": token}
    ).json()
    step = polled["pending_step"]
    assert step["action"] == "observe" and step["seq"] == 1 and polled["status"] == "active"
    assert client.get(f"/api/workflow-runs/{run['id']}", headers=headers).json()["status"] == "waiting_for_harness"
    observation = {
        "observation_id": "obs-1",
        "url": "http://localhost:8765/_sandbox/entry.html",
        "title": "Sandbox",
        "candidates": [{"id": 41, "role": "button", "name": "Submit invoice", "kind": "clickable"}],
    }
    result = {"device_id": "device-1", "lease_token": token, "ok": True, "description": "Looked", "observation": observation}
    posted = client.post(f"/api/recorder/computer-use/steps/{step['step_id']}/result", headers=headers, json=result)
    assert posted.status_code == 200 and posted.json()["duplicate"] is False
    assert (
        client.post(f"/api/recorder/computer-use/steps/{step['step_id']}/result", headers=headers, json=result).json()["duplicate"] is True
    )
    with platform_session() as platform:
        resumes = platform.scalars(select(Job).where(Job.idempotency_key == f"workflow_run:{run['id']}:resume:1")).all()
        assert len(resumes) == 1

    # Resumed: the planner wants to submit → pause for the owner.
    _drain()
    run = client.get(f"/api/workflow-runs/{run['id']}", headers=headers).json()
    assert run["status"] == "waiting_for_human" and run["pending"]["reason"] == "irreversible"
    assert run["pending"]["target"]["label"] == "button: Submit invoice" and run["pending"]["candidates"][0]["p"] == 0.9
    polled = client.get(
        f"/api/recorder/computer-use/sessions/{session_id}", headers=headers, params={"device_id": "device-1", "lease_token": token}
    ).json()
    assert polled["status"] == "paused_for_approval" and polled["pending_step"] is None
    wrong = client.post(
        f"/api/workflow-runs/{run['id']}/decision", headers=headers, json={"step_id": str(uuid.uuid4()), "decision": "approve"}
    )
    assert wrong.status_code == 409
    decided = client.post(
        f"/api/workflow-runs/{run['id']}/decision",
        headers=headers,
        json={"step_id": run["pending"]["step_id"], "decision": "approve", "reason": "ok"},
    )
    assert decided.status_code == 200 and decided.json()["status"] == "queued"

    # Approved: the click is filed for the recorder, answered, then the run finishes and verifies.
    _drain()
    polled = client.get(
        f"/api/recorder/computer-use/sessions/{session_id}", headers=headers, params={"device_id": "device-1", "lease_token": token}
    ).json()
    step = polled["pending_step"]
    assert step["action"] == "click" and step["target_id"] == "41" and step["observation_id"] == "obs-1" and "value" not in step
    done = {
        **result,
        "description": "Clicked Submit invoice",
        "result": {"url_after": "http://localhost:8765/_sandbox/entry.html", "title_after": "Sandbox"},
        "observation": {**observation, "observation_id": "obs-2"},
    }
    assert client.post(f"/api/recorder/computer-use/steps/{step['step_id']}/result", headers=headers, json=done).status_code == 200
    _drain()
    run = client.get(f"/api/workflow-runs/{run['id']}", headers=headers).json()
    assert run["status"] == "succeeded", run
    trace = events(schema, run["agent_run_id"])
    clicks = [d for t, d in trace if t == "tool_call" and d.get("tool") == "submit" and d.get("executed")]
    assert len(clicks) == 1 and clicks[0]["gated"] is True and clicks[0]["value_input"] is None
    assert [t for t, _ in trace].count("handoff") == 1
    with tenant_session(schema) as session:
        steps = session.scalars(
            select(HarnessStep).where(HarnessStep.workflow_run_id == uuid.UUID(run["id"])).order_by(HarnessStep.seq)
        ).all()
        assert [(s.seq, s.harness, s.status) for s in steps] == [(1, "browser", "done"), (2, "browser", "done")]
    # Viewers cannot start, decide or stop.
    with platform_session() as platform:
        company = platform.get(FirmCompany, uuid.UUID(company_id))
        key = uuid.uuid4().hex + uuid.uuid4().hex
        platform.add(User(tenant_id=company.tenant_id, email=f"{uuid.uuid4().hex}@example.com", api_token=key, role="viewer"))
        platform.commit()
    viewer = {"Authorization": f"Bearer {key}"}
    assert client.get(f"/api/workflow-runs/{run['id']}", headers=viewer).status_code == 200
    assert start(client, viewer, workflow, version).status_code == 403
    assert client.post(f"/api/workflow-runs/{run['id']}/stop", headers=viewer, json={}).status_code == 403


def test_limits_fail_the_run_and_a_denied_step_stops_it(client, monkeypatch):
    judge = ScriptedJudge({"action": "wait"}, {"action": "wait"}, {"action": "wait"})
    monkeypatch.setattr("vista.computer_use.handler.judge", judge)
    headers, company_id, schema, _ = company_admin(client)
    workflow, version = approved(
        client, headers, {**DOCS_ONLY, "limits": {"max_steps": 1, "max_runtime_seconds": 300, "max_cost_usd": "1.00"}}, name="Tiny"
    )
    run = start(client, headers, workflow, version).json()
    _drain()
    run = client.get(f"/api/workflow-runs/{run['id']}", headers=headers).json()
    assert run["status"] == "failed" and run["error"] == "step_limit" and run["steps_used"] == 1
    trace = events(schema, run["agent_run_id"])
    assert ("error", {"error": "step_limit", "steps_used": 1, "cost_usd": trace[-2][1]["cost_usd"]}) == trace[-2]
    with tenant_session(schema) as session:
        assert session.scalar(select(Task.title).where(Task.source_type == "agent_run", Task.source_id == run["agent_run_id"])).endswith(
            "step limit"
        )

    # deny
    judge2 = ScriptedJudge({"action": "extract", "target": NONE})
    monkeypatch.setattr("vista.computer_use.handler.judge", judge2)
    workflow, version = approved(client, headers, DOCS_ONLY, name="Deny me")
    run = start(client, headers, workflow, version).json()
    _drain()
    run = client.get(f"/api/workflow-runs/{run['id']}", headers=headers).json()
    assert run["status"] == "waiting_for_human" and run["pending"]["reason"] == "ambiguous_target"
    denied = client.post(
        f"/api/workflow-runs/{run['id']}/decision", headers=headers, json={"step_id": run["pending"]["step_id"], "decision": "deny"}
    )
    assert denied.status_code == 200
    judge2.specs.append({"action": "extract", "target": NONE})
    _drain()
    run = client.get(f"/api/workflow-runs/{run['id']}", headers=headers).json()
    assert run["status"] in ("waiting_for_human", "stopped")  # a denied ambiguous step re-plans; a denied irreversible step stops


def test_an_edited_definition_cannot_run_under_the_old_approval(client, monkeypatch):
    monkeypatch.setattr("vista.computer_use.handler.judge", ScriptedJudge({"action": "done"}, {"goal_met": 0.9, "criterion_0": 0.9}))
    headers, company_id, schema, _ = company_admin(client)
    workflow, version = approved(client, headers, DOCS_ONLY, name="Pinned")
    run = start(client, headers, workflow, version).json()
    with tenant_session(schema) as session:
        header = session.get(WorkflowRun, uuid.UUID(run["id"]))
        header.definition_hash = "0" * 64  # simulate a launch under a definition that no longer matches
        session.commit()
    _drain()
    run = client.get(f"/api/workflow-runs/{run['id']}", headers=headers).json()
    assert run["status"] == "failed" and run["error"] == "definition_hash_mismatch"
    with tenant_session(schema) as session:
        agent_run = session.get(AgentRun, uuid.UUID(run["agent_run_id"]))
        assert agent_run.status == "failed"


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_only_a_tenant_admin_decides_versions(client, role):
    headers, company_id, schema, _ = company_admin(client)
    workflow = client.post("/api/workflows", headers=headers, json={"name": "Roles", "definition": DOCS_ONLY}).json()
    with platform_session() as platform:
        company = platform.get(FirmCompany, uuid.UUID(company_id))
        key = uuid.uuid4().hex + uuid.uuid4().hex
        platform.add(User(tenant_id=company.tenant_id, email=f"{uuid.uuid4().hex}@example.com", api_token=key, role=role))
        platform.commit()
    other = {"Authorization": f"Bearer {key}"}
    version = workflow["latest_version"]
    assert (
        client.post(
            f"/api/workflows/{workflow['id']}/versions/{version['id']}/decision", headers=other, json={"decision": "approved"}
        ).status_code
        == 403
    )
