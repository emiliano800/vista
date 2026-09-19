# Vista

Backend for an AI-agent platform serving lower-middle-market private-equity firms.

See [IMPLEMENTATION.md](IMPLEMENTATION.md) for a full walkthrough of Milestone 1 and a
primer on how the agent machinery works. See
[BUSINESS_PLAN_AND_IDEAS.md](BUSINESS_PLAN_AND_IDEAS.md) for the product vision.

## Architecture (Milestone 1)

- **Python 3.12 + FastAPI + SQLAlchemy 2 + Alembic**, Postgres, S3 (MinIO locally), managed with `uv`.
- **Tenant isolation: schema-per-tenant.** Shared `platform` schema holds `tenants`, `users`, `jobs`. Each tenant gets a `t_<hex>` schema holding `deals`, `deal_memberships`, `documents`, `agent_runs`, `agent_run_events`, `usage_events`. Sessions are scoped via `search_path` in one place (`vista/db.py`).
- **Document permissions:** deal-level roles (`owner` / `member` / `viewer`) checked on every read/write (`vista/permissions.py`). Blobs live in S3 via presigned URLs; Postgres stores metadata only.
- **Durable jobs:** Postgres-backed queue (`FOR UPDATE SKIP LOCKED`) with retries + backoff and idempotency keys (`vista/jobs/queue.py`, worker in `vista/jobs/worker.py`).
- **Agent execution logs:** append-only `agent_runs` / `agent_run_events` per tenant, doubling as the audit trail.
- **Cost tracking:** one `usage_events` row per model call (tokens, unit cost); aggregated by `GET /usage`.

## Getting started

Prerequisites: [uv](https://docs.astral.sh/uv/), Docker (or OrbStack).

```sh
docker compose up -d          # Postgres + MinIO
uv sync
cp .env.example .env
uv run alembic -n platform upgrade head           # shared schema
uv run uvicorn vista.main:app --reload            # API on :8000
uv run python -m vista.jobs.worker                # job worker (separate terminal)
```

Tenant schemas are created and migrated automatically by `POST /tenants`. After adding a
tenant migration, apply it to all tenants with:

```sh
uv run python -c "from vista.tenancy import migrate_all_tenants; migrate_all_tenants()"
```

### Tests

```sh
docker compose up -d
uv run pytest
```

Tests skip automatically if Postgres is unreachable. Key suites: tenant isolation
(`test_tenant_isolation.py`), deal permissions (`test_permissions.py`), job durability +
idempotency + cost tracking (`test_jobs.py`).

## API sketch

| Endpoint | Purpose |
| --- | --- |
| `POST /tenants` | Provision firm: schema + owner user + API token (unauthenticated in M1 — gate before deploying) |
| `POST /deals`, `GET /deals` | Deals scoped to the caller's memberships |
| `POST /deals/{id}/documents` | Register doc, returns presigned S3 upload URL (member+) |
| `GET /documents/{id}` | Metadata + presigned download URL (viewer+) |
| `POST /runs` | Enqueue a durable `agent_run` job (supports `idempotency_key`) |
| `GET /runs/{id}` | Run status + append-only event log |
| `GET /usage` | Tenant-wide token/cost totals |
| `POST /employees`, `GET /employees` | Employee registry (admin creates, members view) |
| `POST /agents`, `GET /agents`, `PATCH /agents/{id}` | One agent per employee: scopes, schedule (hourly/daily/weekly), pause/resume |
| `POST /agents/{id}/runs` | Trigger an on-demand discovery run (also scheduled via `python -m vista.jobs.scheduler`) |
| `GET /findings`, `PATCH /findings/{id}` | Evidence-labeled findings (`observed_fact`/`inefficiency`/`proposed_automation`); triage open→reviewed/dismissed/actioned |
| `POST /summaries`, `GET /summaries`, `GET /summaries/latest` | Company summary runs aggregating all open findings |

## Agent vision: autonomous per-employee agents

The end-state is a three-level hierarchy of autonomous agents:

```
Portfolio summary   — cross-company synergies and shared inefficiencies
  └── Company summary   — roll-up of everything that company's agents found
       └── Employee agent   — one per back-office employee
```

Each **employee agent** is given only the person's role ("bookkeeper at Cedar
Climate") and autonomously discovers how they actually work — the systems they use,
the repetitive sequences they perform, where time is lost — then proposes and
eventually executes automations. Employees don't operate the agents; they answer a
few onboarding questions and approve or correct what the agent surfaces. A **company
summary agent** aggregates all employee-agent findings into one operational picture
per business, and the **portfolio layer** compares companies.

Autonomy widens in phases (same architecture throughout, only the sensors and
permissions grow):

| Phase | Employee agent's access | Purpose |
| --- | --- | --- |
| 1. Records agent (current — registry, findings, and summaries are built) | Documents, imported files; role-based hypotheses | Extract facts, flag inconsistencies |
| 2. Connector agent (next) | Read-only email, accounting (QuickBooks/Xero), files, calendar | Reconstruct real workflows from digital exhaust; find repetition and double-entry |
| 3. Shadow agent | + guided interviews, observing approved apps | Validate process maps with the employee |
| 4. Autonomous agent | Scoped computer-use sessions; executes fixes | Do the work; route exceptions to a human |

Every finding stays evidence-linked (observed fact vs. inefficiency vs. proposed
automation) and every agent action lands in the append-only run log — that audit
trail is what makes rising autonomy acceptable to PE owners and employees.

The current backend already supports this shape: tenants = companies, durable jobs =
long-running agent loops, `agent_runs`/`agent_run_events` = the audit trail,
`usage_events` = per-agent cost. Next build steps: an employee/agent registry, a
scheduler for recurring discovery runs, a `findings` table, the company summary run
type, and the first two connectors (email + QuickBooks).

## Notes / deferred

Billing, per-document ACLs, RAG/embeddings, SSO/SCIM, Temporal migration, multi-region.
Agent runs call OpenAI when `VISTA_OPENAI_API_KEY` is set (see `.env.example`); otherwise
the handler in `vista/jobs/handlers.py` falls back to a stub. Try the end-to-end demo with
`uv run python scripts/demo.py`.
