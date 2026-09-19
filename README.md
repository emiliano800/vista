# Vista

Backend for an AI-agent platform serving lower-middle-market private-equity firms.

Docs:
- [IMPLEMENTATION_SO_FAR.md](IMPLEMENTATION_SO_FAR.md) — everything built, how it works, agent primer
- [FURTHER_STEPS.md](FURTHER_STEPS.md) — technical roadmap: frontend spec, connectors, autonomy ladder
- [BUSINESS_COURSE_OF_ACTION.md](BUSINESS_COURSE_OF_ACTION.md) — business plan distilled + go-to-market steps

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

## Agent vision (short version)

Three-level hierarchy: **employee agent** (one per back-office worker, knows only the
role, discovers inefficiencies) → **company summary** → **portfolio summary**.
Autonomy widens in phases — records agent (built) → read-only connectors → shadowing →
scoped execution — while the employee-facing surface stays minimal. Full roadmap and
frontend spec: [FURTHER_STEPS.md](FURTHER_STEPS.md).

## Notes / deferred

Billing, per-document ACLs, RAG/embeddings, SSO/SCIM, Temporal migration, multi-region.
Agent runs call OpenAI when `VISTA_OPENAI_API_KEY` is set (see `.env.example`); otherwise
the handler in `vista/jobs/handlers.py` falls back to a stub. Try the end-to-end demo with
`uv run python scripts/demo.py`.
