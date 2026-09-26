"""Enqueues recurring employee-agent discovery runs.

uv run python -m vista.jobs.scheduler   # loop; or call enqueue_due() from cron
"""

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from vista.db import platform_session, tenant_session
from vista.jobs.queue import enqueue, find_job
from vista.models.platform import Tenant
from vista.models.tenant import AgentRun, Employee, EmployeeAgent

log = logging.getLogger("vista.scheduler")

SCHEDULE_INTERVALS = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
}


def enqueue_due(now: datetime | None = None) -> int:
    """Enqueue a discovery run for every active agent whose interval has elapsed.
    Returns the number of runs enqueued. Idempotent per (agent, due window)."""
    now = now or datetime.now(UTC)
    enqueued = 0
    with platform_session() as psession:
        tenants = psession.execute(select(Tenant.id, Tenant.schema_name)).all()
    for tenant_id, schema in tenants:
        with tenant_session(schema) as session:
            agents = session.scalars(select(EmployeeAgent).where(EmployeeAgent.status == "active")).all()
            for agent in agents:
                interval = SCHEDULE_INTERVALS.get(agent.schedule, SCHEDULE_INTERVALS["daily"])
                if agent.last_run_at is not None and agent.last_run_at + interval > now:
                    continue
                employee = session.get(Employee, agent.employee_id)
                window = now.strftime("%Y%m%d%H") if agent.schedule == "hourly" else now.strftime("%Y%m%d")
                key = f"discovery:{agent.id}:{window}"
                with platform_session() as psession:
                    already = find_job(psession, tenant_id, "employee_discovery", key) is not None
                if already:
                    # This slot was filed by an earlier pass; no second run row for the same job.
                    agent.last_run_at = now
                    session.commit()
                    continue
                run = AgentRun(
                    job_id=uuid.uuid4(),
                    run_type="employee_discovery",
                    employee_agent_id=agent.id,
                    deal_id=agent.deal_id,
                    agent_key="file_reviewer",
                    requested_by=employee.id,  # scheduled on the employee's behalf
                )
                session.add(run)
                session.flush()
                with platform_session() as psession:
                    job = enqueue(
                        psession,
                        tenant_id=tenant_id,
                        kind="employee_discovery",
                        payload={"run_id": str(run.id)},
                        idempotency_key=key,
                    )
                    psession.commit()
                run.job_id = job.id
                # Claim the slot so the next scheduler pass doesn't double-enqueue.
                agent.last_run_at = now
                session.commit()
                enqueued += 1
    return enqueued


def run_forever(poll_interval: float = 60.0) -> None:
    logging.basicConfig(level=logging.INFO)
    log.info("scheduler started")
    while True:
        n = enqueue_due()
        if n:
            log.info("enqueued %d discovery runs", n)
        time.sleep(poll_interval)


if __name__ == "__main__":
    run_forever()
