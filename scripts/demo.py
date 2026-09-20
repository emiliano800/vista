"""End-to-end demo / smoke test. Requires Postgres running (docker compose up -d).

    uv run python scripts/demo.py

Provisions a throwaway tenant, creates a deal + document, enqueues an agent run,
processes it with the worker, and prints the run's event log and tenant usage.
If VISTA_OPENAI_API_KEY is set in .env, the model_call step hits OpenAI for real
(one small gpt-4o-mini call); otherwise it uses the built-in stub.
"""

import uuid

from sqlalchemy import select

from vista.config import settings
from vista.db import platform_session, tenant_session
from vista.jobs.queue import enqueue
from vista.jobs.worker import process_one
from vista.models.tenant import AgentRun, AgentRunEvent, Deal, DealMembership, Document, UsageEvent
from vista.tenancy import migrate_platform, provision_tenant


def main() -> None:
    mode = "REAL OpenAI call" if settings.openai_api_key else "stub model (no API key set)"
    print(f"Mode: {mode}\n")

    migrate_platform()
    tenant, owner, _token = provision_tenant(f"demo-firm-{uuid.uuid4().hex[:6]}", "owner@demo.example.com")
    print(f"Provisioned tenant {tenant.name!r} (schema {tenant.schema_name})")

    with tenant_session(tenant.schema_name) as session:
        deal = Deal(name="Project Cedar", created_by=owner.id)
        session.add(deal)
        session.flush()
        session.add(DealMembership(deal_id=deal.id, user_id=owner.id, role="owner"))
        doc = Document(
            deal_id=deal.id,
            filename="cedar-climate-cim.pdf",
            s3_key=f"{tenant.schema_name}/deals/{deal.id}/demo/cedar-climate-cim.pdf",
            uploaded_by=owner.id,
        )
        session.add(doc)
        session.flush()
        run = AgentRun(job_id=uuid.uuid4(), deal_id=deal.id, document_id=doc.id, requested_by=owner.id)
        session.add(run)
        session.flush()
        run_id = run.id
        session.commit()

    with platform_session() as psession:
        job = enqueue(psession, tenant_id=tenant.id, kind="agent_run", payload={"run_id": str(run_id)})
        psession.commit()
    print(f"Enqueued job {job.id}, processing...\n")
    while process_one():
        pass

    with tenant_session(tenant.schema_name) as session:
        run = session.get(AgentRun, run_id)
        print(f"Run status: {run.status}\n\nEvent log:")
        for e in session.scalars(select(AgentRunEvent).where(AgentRunEvent.run_id == run_id).order_by(AgentRunEvent.seq)):
            print(f"  [{e.seq}] {e.event_type}: {e.data}")
        print("\nUsage:")
        for u in session.scalars(select(UsageEvent).where(UsageEvent.run_id == run_id)):
            print(f"  model={u.model} in={u.input_tokens} out={u.output_tokens} cost=${u.cost_usd:.6f}")


if __name__ == "__main__":
    main()
