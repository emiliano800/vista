import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

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


def claim_next(session: Session) -> Job | None:
    """Claim one due job using FOR UPDATE SKIP LOCKED. Marks it running and
    increments attempts. Caller must commit to release the row lock."""
    row = session.execute(
        text(
            """
            UPDATE platform.jobs
            SET status = 'running', attempts = attempts + 1, updated_at = now()
            WHERE id = (
                SELECT id FROM platform.jobs
                WHERE status = 'queued' AND run_at <= now()
                ORDER BY run_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id
            """
        )
    ).first()
    if row is None:
        return None
    return session.get(Job, row[0])


def mark_succeeded(session: Session, job: Job) -> None:
    job.status = "succeeded"
    job.error = None


def mark_failed(session: Session, job: Job, error: str) -> None:
    """Retry with backoff until max_attempts, then fail permanently."""
    job.error = error[:4000]
    if job.attempts >= job.max_attempts:
        job.status = "failed"
    else:
        backoff = RETRY_BACKOFF_SECONDS[min(job.attempts - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
        job.status = "queued"
        job.run_at = datetime.now(UTC) + timedelta(seconds=backoff)
