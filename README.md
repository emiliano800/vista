# Vista

Desktop task mining and company workspaces for lower-middle-market private-equity firms.

## First web workspace

The browser dashboard in `src/web/public` displays completed recording reports uploaded
from Electron. It includes personal access-key sign-in, company selection, source evidence
for automation recommendations, and report downloads. Reports are isolated by tenant and
company membership; raw desktop events, screenshots, and video stay local.

See [deploy/README.md](deploy/README.md) for local startup, Docker hosting, user
provisioning, and the Cloudflare configuration for `bumpsolutions.org`. Cloudflare serves
the frontend and proxies to a separately hosted FastAPI/Postgres/S3 backend.



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
uv run python -m vista.manage migrate             # shared + tenant schemas and bucket
VISTA_COOKIE_SECURE=false uv run uvicorn vista.main:app --reload  # local HTTP on :8000
uv run python -m vista.jobs.worker                # job worker (separate terminal)
```

Tenant schemas are created and migrated by `python -m vista.manage create-workspace`.
Public `POST /tenants` is disabled unless an operator provisioning key is configured. After adding a
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
| `POST /tenants` | Operator-only provisioning, disabled by default; prefer `vista.manage create-workspace` |
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
