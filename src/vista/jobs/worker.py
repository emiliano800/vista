import logging
import os
import socket
import threading
import time
import uuid

from sqlalchemy import select

from vista.config import settings
from vista.db import platform_session
from vista.jobs.handlers import AFTER_TERMINAL, HANDLERS, mark_run_failed
from vista.jobs.queue import claim_next, mark_failed, mark_succeeded, reap_expired, renew_lease
from vista.models.platform import Job, Tenant

log = logging.getLogger("vista.worker")

# One identity per worker process: the lease owner on the jobs it is running.
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def _heartbeat(job_id: uuid.UUID, stop: threading.Event, lost: threading.Event) -> None:
    """Renew the job's lease every third of its length while the handler runs. If the
    renewal is refused the reaper has taken the job back; the result is then dropped."""
    interval = max(0.2, settings.job_lease_s / 3)
    while not stop.wait(interval):
        try:
            with platform_session() as session:
                ok = renew_lease(session, job_id, WORKER_ID)
                session.commit()
        except Exception:  # noqa: BLE001 — a missed heartbeat is not fatal; the next one may land
            log.exception("lease renewal for job %s failed", job_id)
            continue
        if not ok:
            lost.set()
            log.warning("lease on job %s was lost; its result will not be recorded", job_id)
            return


def process_one() -> bool:
    """Claim and process a single job. Returns True if a job was processed."""
    with platform_session() as session:
        job = claim_next(session, owner=WORKER_ID)
        if job is None:
            return False
        schema = session.scalar(select(Tenant.schema_name).where(Tenant.id == job.tenant_id))
        session.commit()  # release the claim lock; job is now 'running' and leased to us

    stop, lost = threading.Event(), threading.Event()
    beat = threading.Thread(target=_heartbeat, args=(job.id, stop, lost), name=f"lease-{job.id}", daemon=True)
    beat.start()
    handler = HANDLERS.get(job.kind)
    error: str | None = None
    try:
        if handler is None:
            error = f"no handler for job kind {job.kind!r}"
        else:
            try:
                handler(job, schema)
            except Exception as exc:  # noqa: BLE001 — worker boundary
                log.exception("job %s failed", job.id)
                error = repr(exc)
    finally:
        stop.set()
        beat.join(timeout=5)

    with platform_session() as session:
        job = session.get(Job, job.id)
        if lost.is_set() or job.lease_owner != WORKER_ID:
            # Another attempt owns this job now; whatever we did is theirs to redo.
            log.warning("job %s is no longer leased to this worker; result dropped", job.id)
            return True
        if error is None:
            mark_succeeded(session, job)
        else:
            mark_failed(session, job, error)
            if job.kind in HANDLERS:
                mark_run_failed(job, schema, error, permanent=job.status == "failed")
        session.commit()
        terminal = job.status in ("succeeded", "failed")
        kind, job_id = job.kind, job.id

    _after_terminal(kind, job_id, terminal)
    return True


def _after_terminal(kind: str, job_id: uuid.UUID, terminal: bool) -> None:
    # Barriers run after the terminal status is committed, so a sibling reading the queue sees it.
    hook = AFTER_TERMINAL.get(kind)
    if terminal and hook is not None:
        try:
            hook(job_id)
        except Exception:  # noqa: BLE001 — worker boundary; interpretation_status re-tries the barrier on each poll
            log.exception("after-terminal hook for job %s failed", job_id)


def reap_stuck_jobs() -> int:
    """Re-queue (or fail) jobs whose worker stopped renewing its lease, and settle their
    AgentRun rows the way a failed attempt would. Returns how many jobs were touched."""
    with platform_session() as session:
        reaped = reap_expired(session)
        settled = [
            (job.id, job.kind, job.status, session.scalar(select(Tenant.schema_name).where(Tenant.id == job.tenant_id)), dict(job.payload))
            for job in reaped
        ]
        session.commit()
    for job_id, kind, status, schema, payload in settled:
        log.warning("job %s (%s) lost its lease; %s", job_id, kind, "failed permanently" if status == "failed" else "re-queued")
        if kind in HANDLERS and schema:
            try:
                stub = Job(id=job_id, kind=kind, payload=payload)
                mark_run_failed(stub, schema, "worker stopped answering (lease expired)", permanent=status == "failed")
            except Exception:  # noqa: BLE001 — the job itself is already re-queued or failed
                log.exception("could not settle the run of reaped job %s", job_id)
        _after_terminal(kind, job_id, status == "failed")
    return len(settled)


def run_forever(poll_interval: float = 1.0) -> None:
    logging.basicConfig(level=logging.INFO)
    log.info("worker %s started", WORKER_ID)
    last_reap = 0.0
    while True:
        if time.monotonic() - last_reap >= settings.job_reap_interval_s:
            try:
                reap_stuck_jobs()
            except Exception:  # noqa: BLE001 — worker boundary
                log.exception("reaping stuck jobs failed")
            last_reap = time.monotonic()
        if not process_one():
            time.sleep(poll_interval)


if __name__ == "__main__":
    run_forever()
