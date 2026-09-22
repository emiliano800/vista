import logging
import time

from sqlalchemy import select

from vista.db import platform_session
from vista.jobs.handlers import AFTER_TERMINAL, HANDLERS, mark_run_failed
from vista.jobs.queue import claim_next, mark_failed, mark_succeeded
from vista.models.platform import Tenant

log = logging.getLogger("vista.worker")


def process_one() -> bool:
    """Claim and process a single job. Returns True if a job was processed."""
    with platform_session() as session:
        job = claim_next(session)
        if job is None:
            return False
        schema = session.scalar(select(Tenant.schema_name).where(Tenant.id == job.tenant_id))
        session.commit()  # release the claim lock; job is now 'running'

    handler = HANDLERS.get(job.kind)
    error: str | None = None
    if handler is None:
        error = f"no handler for job kind {job.kind!r}"
    else:
        try:
            handler(job, schema)
        except Exception as exc:  # noqa: BLE001 — worker boundary
            log.exception("job %s failed", job.id)
            error = repr(exc)

    with platform_session() as session:
        job = session.merge(job)
        if error is None:
            mark_succeeded(session, job)
        else:
            mark_failed(session, job, error)
            if job.kind in HANDLERS:
                mark_run_failed(job, schema, error, permanent=job.status == "failed")
        session.commit()
        terminal = job.status in ("succeeded", "failed")
        kind, job_id = job.kind, job.id

    # Barriers run after the terminal status is committed, so a sibling reading the queue sees it.
    hook = AFTER_TERMINAL.get(kind)
    if terminal and hook is not None:
        try:
            hook(job_id)
        except Exception:  # noqa: BLE001 — worker boundary; interpretation_status re-tries the barrier on each poll
            log.exception("after-terminal hook for job %s failed", job_id)
    return True


def run_forever(poll_interval: float = 1.0) -> None:
    logging.basicConfig(level=logging.INFO)
    log.info("worker started")
    while True:
        if not process_one():
            time.sleep(poll_interval)


if __name__ == "__main__":
    run_forever()
