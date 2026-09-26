import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from vista.config import settings
from vista.models.platform import Job

RETRY_BACKOFF_SECONDS = [10, 60, 300]


def enqueue(
    session: Session,
    tenant_id: uuid.UUID,
    kind: str,
    payload: dict,
    idempotency_key: str | None = None,
    max_attempts: int = 3,
    run_at: datetime | None = None,
) -> Job:
    """Insert a job. If an idempotency_key is given and a job already exists for
    (tenant, kind, key), return the existing job instead of inserting. `run_at` defers it."""
    if idempotency_key is not None:
        existing = session.scalar(
            select(Job).where(
                Job.tenant_id == tenant_id,
                Job.kind == kind,
                Job.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing
    job = Job(
        tenant_id=tenant_id,
        kind=kind,
        payload=payload,
        idempotency_key=idempotency_key,
        max_attempts=max_attempts,
    )
    if run_at is not None:
        job.run_at = run_at  # a deferred job (e.g. a watchdog) is not due until then
    session.add(job)
    session.flush()
    return job


def claim_next(session: Session, owner: str = "worker", lease_seconds: int | None = None) -> Job | None:
    """Claim one due job using FOR UPDATE SKIP LOCKED. Marks it running, increments
    attempts and leases it to `owner` for `lease_seconds` (the worker renews the lease
    while its handler runs). Caller must commit to release the row lock."""
    lease = settings.job_lease_s if lease_seconds is None else lease_seconds
    row = session.execute(
        text(
            """
            UPDATE platform.jobs
            SET status = 'running', attempts = attempts + 1, updated_at = now(),
                lease_owner = :owner, lease_until = now() + make_interval(secs => :lease)
            WHERE id = (
                SELECT id FROM platform.jobs
                WHERE status = 'queued' AND run_at <= now()
                ORDER BY run_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id
            """
        ),
        {"owner": owner, "lease": float(lease)},
    ).first()
    if row is None:
        return None
    return session.get(Job, row[0])


def renew_lease(session: Session, job_id: uuid.UUID, owner: str, lease_seconds: int | None = None) -> bool:
    """Extend the lease the worker holds on a running job. False when the lease is no
    longer this worker's (the reaper took it back), so the caller must not report a result."""
    lease = settings.job_lease_s if lease_seconds is None else lease_seconds
    result = session.execute(
        text(
            """
            UPDATE platform.jobs
            SET lease_until = now() + make_interval(secs => :lease), updated_at = now()
            WHERE id = :id AND status = 'running' AND lease_owner = :owner
            """
        ),
        {"id": job_id, "owner": owner, "lease": float(lease)},
    )
    return result.rowcount == 1


LEASE_EXPIRED = "lease expired: the worker running this job stopped answering"


def reap_expired(session: Session) -> list[Job]:
    """Take back every running job whose lease has lapsed: re-queue it to run now, or fail
    it when its attempts are spent. Returns the jobs touched (status already updated,
    not yet committed) so the worker can settle their AgentRun rows."""
    ids = [
        row[0]
        for row in session.execute(
            text(
                """
                SELECT id FROM platform.jobs
                WHERE status = 'running' AND lease_until IS NOT NULL AND lease_until < now()
                FOR UPDATE SKIP LOCKED
                """
            )
        ).all()
    ]
    reaped = []
    for job_id in ids:
        job = session.get(Job, job_id)
        job.lease_owner, job.lease_until = None, None
        job.error = LEASE_EXPIRED
        if job.attempts >= job.max_attempts:
            job.status = "failed"
        else:
            job.status = "queued"
            job.run_at = datetime.now(UTC)
        reaped.append(job)
    session.flush()
    return reaped


def mark_succeeded(session: Session, job: Job) -> None:
    job.status = "succeeded"
    job.error = None
    job.lease_owner, job.lease_until = None, None


def mark_failed(session: Session, job: Job, error: str) -> None:
    """Retry with backoff until max_attempts, then fail permanently."""
    job.error = error[:4000]
    job.lease_owner, job.lease_until = None, None
    if job.attempts >= job.max_attempts:
        job.status = "failed"
    else:
        backoff = RETRY_BACKOFF_SECONDS[min(job.attempts - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
        job.status = "queued"
        job.run_at = datetime.now(UTC) + timedelta(seconds=backoff)
