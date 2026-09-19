import uuid

from sqlalchemy import select

from vista.db import platform_session
from vista.jobs.queue import enqueue
from vista.jobs.worker import process_one
from vista.models.platform import Job

from tests.conftest import requires_db

pytestmark = requires_db


def _drain(max_jobs: int = 20) -> None:
    for _ in range(max_jobs):
        if not process_one():
            return


def test_agent_run_end_to_end(client, tenant_factory):
    headers, _, _ = tenant_factory()
    deal = client.post("/deals", json={"name": "Project Kestrel"}, headers=headers).json()
    doc = client.post(
        f"/deals/{deal['id']}/documents", json={"filename": "cim.pdf"}, headers=headers
    ).json()
    run = client.post(
        "/runs", json={"deal_id": deal["id"], "document_id": doc["id"]}, headers=headers
    ).json()
    assert run["status"] == "queued"

    _drain()

    result = client.get(f"/runs/{run['id']}", headers=headers).json()
    assert result["status"] == "succeeded"
    types = [e["event_type"] for e in result["events"]]
    assert types == ["step", "tool_call", "model_call", "result"]

    usage = client.get("/usage", headers=headers).json()
    assert usage["runs"] == 1
    assert usage["total_input_tokens"] > 0
    assert float(usage["total_cost_usd"]) > 0


def test_idempotency_key_dedupes_jobs(client, tenant_factory):
    headers, tenant_id, _ = tenant_factory()
    deal = client.post("/deals", json={"name": "Project Merlin"}, headers=headers).json()
    key = f"once-{uuid.uuid4().hex}"

    r1 = client.post("/runs", json={"deal_id": deal["id"], "idempotency_key": key}, headers=headers)
    r2 = client.post("/runs", json={"deal_id": deal["id"], "idempotency_key": key}, headers=headers)
    assert r1.status_code == r2.status_code == 201

    with platform_session() as session:
        jobs = session.scalars(
            select(Job).where(Job.tenant_id == tenant_id, Job.idempotency_key == key)
        ).all()
    assert len(jobs) == 1


def test_failed_job_is_retried_then_fails_permanently(tenant_factory, client):
    _, tenant_id, _ = tenant_factory()
    # agent_run with a run_id that doesn't exist -> handler raises every time.
    with platform_session() as session:
        job = enqueue(
            session,
            tenant_id=uuid.UUID(tenant_id),
            kind="agent_run",
            payload={"run_id": str(uuid.uuid4())},
            max_attempts=2,
        )
        session.commit()
        job_id = job.id

    _drain()
    with platform_session() as session:
        job = session.get(Job, job_id)
        assert job.status == "queued"  # retry scheduled with backoff
        assert job.attempts == 1
        assert job.error is not None
        # Force the retry to be due now, then process again.
        from sqlalchemy import text

        session.execute(text("UPDATE platform.jobs SET run_at = now() WHERE id = :id"), {"id": job_id})
        session.commit()

    _drain()
    with platform_session() as session:
        job = session.get(Job, job_id)
        assert job.status == "failed"
        assert job.attempts == 2
