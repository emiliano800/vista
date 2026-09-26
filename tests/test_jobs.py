import uuid

from sqlalchemy import select

from tests.conftest import requires_db
from vista.config import settings
from vista.db import platform_session
from vista.jobs.handlers import HANDLERS
from vista.jobs.queue import LEASE_EXPIRED, claim_next, enqueue, reap_expired, renew_lease
from vista.jobs.worker import process_one, reap_stuck_jobs
from vista.models.platform import Job

pytestmark = requires_db


def _drain(max_jobs: int = 20) -> None:
    for _ in range(max_jobs):
        if not process_one():
            return


def test_agent_run_end_to_end(client, tenant_factory):
    headers, _, _ = tenant_factory()
    deal = client.post("/deals", json={"name": "Project Kestrel"}, headers=headers).json()
    doc = client.post(f"/deals/{deal['id']}/documents", json={"filename": "cim.pdf"}, headers=headers).json()
    run = client.post("/runs", json={"deal_id": deal["id"], "document_id": doc["id"]}, headers=headers).json()
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
        jobs = session.scalars(select(Job).where(Job.tenant_id == tenant_id, Job.idempotency_key == key)).all()
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


def _expire_lease(job_id) -> None:
    from sqlalchemy import text

    with platform_session() as session:
        session.execute(text("UPDATE platform.jobs SET lease_until = now() - interval '1 second' WHERE id = :id"), {"id": job_id})
        session.commit()


def test_a_job_whose_worker_died_is_reaped_and_re_run(client, tenant_factory):
    headers, _, _ = tenant_factory()
    deal = client.post("/deals", json={"name": "Project Heron"}, headers=headers).json()
    run = client.post("/runs", json={"deal_id": deal["id"]}, headers=headers).json()

    # A worker claims the job and then vanishes (deploy, crash): the job stays `running`
    # with a lease that nobody renews.
    with platform_session() as session:
        job = claim_next(session, owner="dead-worker")
        assert job is not None and job.status == "running" and job.lease_owner == "dead-worker"
        assert job.lease_until is not None
        session.commit()
        job_id = job.id
    assert process_one() is False, "a leased job is not claimable while its lease holds"
    with platform_session() as session:
        assert reap_expired(session) == [], "a live lease is left alone"
        assert renew_lease(session, job_id, "someone-else") is False, "only the owner renews"
        assert renew_lease(session, job_id, "dead-worker") is True

    _expire_lease(job_id)
    assert reap_stuck_jobs() == 1
    with platform_session() as session:
        job = session.get(Job, job_id)
        assert job.status == "queued" and job.lease_owner is None and job.error == LEASE_EXPIRED
        assert job.attempts == 1
    events = client.get(f"/runs/{run['id']}", headers=headers).json()
    assert events["status"] == "queued"
    assert events["events"][-1]["event_type"] == "error" and "lease expired" in events["events"][-1]["data"]["error"]

    # The next worker picks it up and finishes the work.
    _drain()
    result = client.get(f"/runs/{run['id']}", headers=headers).json()
    assert result["status"] == "succeeded"
    with platform_session() as session:
        job = session.get(Job, job_id)
        assert job.status == "succeeded" and job.attempts == 2 and job.lease_owner is None


def test_a_reaped_job_out_of_attempts_fails_its_run(client, tenant_factory):
    headers, _, _ = tenant_factory()
    deal = client.post("/deals", json={"name": "Project Egret"}, headers=headers).json()
    run = client.post("/runs", json={"deal_id": deal["id"]}, headers=headers).json()
    with platform_session() as session:
        job = claim_next(session, owner="dead-worker")
        job.max_attempts = 1
        session.commit()
        job_id = job.id
    _expire_lease(job_id)
    assert reap_stuck_jobs() == 1
    with platform_session() as session:
        assert session.get(Job, job_id).status == "failed"
    assert client.get(f"/runs/{run['id']}", headers=headers).json()["status"] == "failed"
    assert process_one() is False


def test_a_long_handler_keeps_its_lease_through_heartbeats(tenant_factory, monkeypatch):
    _, tenant_id, _ = tenant_factory()
    monkeypatch.setattr(settings, "job_lease_s", 1)  # heartbeat every ~0.33 s
    seen: dict = {}

    def slow_handler(job, schema):
        import time

        time.sleep(1.6)  # longer than the lease: only the heartbeat keeps this job ours
        with platform_session() as session:
            seen["reaped_mid_run"] = len(reap_expired(session))
            seen["lease_until"] = session.get(Job, job.id).lease_until
            session.rollback()

    monkeypatch.setitem(HANDLERS, "slow_test_job", slow_handler)
    with platform_session() as session:
        job = enqueue(session, tenant_id=uuid.UUID(tenant_id), kind="slow_test_job", payload={})
        session.commit()
        job_id = job.id
    assert process_one() is True
    assert seen["reaped_mid_run"] == 0
    with platform_session() as session:
        job = session.get(Job, job_id)
        assert job.status == "succeeded" and job.error is None and job.lease_until is None
