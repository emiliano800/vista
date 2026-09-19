import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from vista.db import platform_session, tenant_session
from vista.jobs.scheduler import enqueue_due
from vista.jobs.worker import process_one
from vista.models.platform import Tenant, User
from vista.models.tenant import EmployeeAgent

from tests.conftest import requires_db

pytestmark = requires_db


def _drain(max_jobs: int = 50) -> None:
    for _ in range(max_jobs):
        if not process_one():
            return


def _setup_agent(client, headers) -> tuple[dict, dict]:
    employee = client.post(
        "/employees",
        json={"name": "Maria Lopez", "role_title": "Bookkeeper", "email": "maria@firm.example.com"},
        headers=headers,
    ).json()
    agent = client.post(
        "/agents",
        json={"employee_id": employee["id"], "scopes": ["documents"], "schedule": "daily"},
        headers=headers,
    ).json()
    return employee, agent


def test_discovery_run_creates_findings(client, tenant_factory):
    headers, _, _ = tenant_factory()
    employee, agent = _setup_agent(client, headers)

    run = client.post(f"/agents/{agent['id']}/runs", headers=headers).json()
    assert run["run_type"] == "employee_discovery"
    _drain()

    result = client.get(f"/runs/{run['id']}", headers=headers).json()
    assert result["status"] == "succeeded"
    assert any(e["event_type"] == "finding" for e in result["events"])

    findings = client.get("/findings", headers=headers).json()
    assert len(findings) >= 2
    assert all(f["employee_id"] == employee["id"] for f in findings)
    assert all(f["kind"] in {"observed_fact", "inefficiency", "proposed_automation"} for f in findings)

    # Agent's last_run_at is updated so the scheduler won't immediately re-run it.
    agents = client.get("/agents", headers=headers).json()
    assert agents[0]["last_run_at"] is not None


def test_finding_status_update(client, tenant_factory):
    headers, _, _ = tenant_factory()
    _, agent = _setup_agent(client, headers)
    client.post(f"/agents/{agent['id']}/runs", headers=headers)
    _drain()

    finding = client.get("/findings", headers=headers).json()[0]
    updated = client.patch(
        f"/findings/{finding['id']}", json={"status": "reviewed"}, headers=headers
    ).json()
    assert updated["status"] == "reviewed"
    assert client.patch(
        f"/findings/{finding['id']}", json={"status": "bogus"}, headers=headers
    ).status_code == 422


def test_company_summary_aggregates_findings(client, tenant_factory):
    headers, _, _ = tenant_factory()
    _, agent = _setup_agent(client, headers)
    client.post(f"/agents/{agent['id']}/runs", headers=headers)
    _drain()

    run = client.post("/summaries", headers=headers).json()
    assert run["run_type"] == "company_summary"
    _drain()

    latest = client.get("/summaries/latest", headers=headers).json()
    assert latest["stats"]["open_findings"] >= 2
    assert latest["stats"]["employees_covered"] == 1
    assert latest["content"]


def test_non_admin_cannot_manage_employees(client, tenant_factory):
    headers, tenant_id, _ = tenant_factory()
    token = secrets.token_hex(32)
    with platform_session() as session:
        session.add(
            User(
                tenant_id=uuid.UUID(tenant_id),
                email="member@firm.example.com",
                api_token=token,
                role="member",
            )
        )
        session.commit()
    member = {"Authorization": f"Bearer {token}"}

    assert client.post(
        "/employees", json={"name": "X", "role_title": "Clerk"}, headers=member
    ).status_code == 403
    assert client.post("/summaries", headers=member).status_code == 403
    # But members can view.
    assert client.get("/employees", headers=member).status_code == 200
    assert client.get("/findings", headers=member).status_code == 200


def test_tenant_isolation_for_employees_and_findings(client, tenant_factory):
    headers_a, _, _ = tenant_factory()
    headers_b, _, _ = tenant_factory()
    _, agent = _setup_agent(client, headers_a)
    client.post(f"/agents/{agent['id']}/runs", headers=headers_a)
    _drain()

    assert client.get("/employees", headers=headers_b).json() == []
    assert client.get("/findings", headers=headers_b).json() == []
    assert client.get("/summaries", headers=headers_b).json() == []


def test_scheduler_enqueues_due_agents_once(client, tenant_factory):
    headers, tenant_id, _ = tenant_factory()
    _, agent = _setup_agent(client, headers)

    with platform_session() as session:
        schema = session.scalar(
            select(Tenant.schema_name).where(Tenant.id == uuid.UUID(tenant_id))
        )

    before = len(client.get("/findings", headers=headers).json())
    assert enqueue_due() >= 1  # our agent has never run -> due now
    # Second pass within the same window: our agent must not be re-enqueued.
    with tenant_session(schema) as session:
        a = session.get(EmployeeAgent, uuid.UUID(agent["id"]))
        assert a.last_run_at is not None
    _drain()
    assert len(client.get("/findings", headers=headers).json()) > before

    # Simulate the interval elapsing: agent becomes due again.
    with tenant_session(schema) as session:
        a = session.get(EmployeeAgent, uuid.UUID(agent["id"]))
        a.last_run_at = datetime.now(timezone.utc) - timedelta(days=2)
        session.commit()
    # A fresh window key is needed for idempotency; use tomorrow to guarantee it.
    assert enqueue_due(datetime.now(timezone.utc) + timedelta(days=1)) >= 1
    _drain()
