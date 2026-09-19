# Vista — Agent/Project Context

## What Vista is

An adaptive operating platform for lower-middle-market private equity. PE firms onboard
acquired businesses (spreadsheets, paper records, fragmented software) into
company-specific workspaces (CRM, invoices, tasks, workflows). AI agents discover facts
from documents, propose configurations, execute bounded administrative work, and analyze
the portfolio for cross-company synergies (purchasing, software overlap, cross-selling).
Initial industry focus: HVAC roll-ups. Near-term goal: 24-hour hackathon prototype with
a portfolio dashboard, three synthetic HVAC companies (Harbor Heating, Summit Mechanical,
Cedar Climate), live acquisition onboarding, and an evidence-backed synergy agent.
Full details: BUSINESS_PLAN_AND_IDEAS.md. Milestone 1 walkthrough: IMPLEMENTATION.md.

Key product principles (from the business plan):
- Distinguish observed facts vs recommendations vs realized results; every agent finding
  cites source records.
- Application code validates, calculates, and enforces permissions; the LLM interprets
  and proposes. No arbitrary agent changes to production systems.
- Company data stays separated; portfolio analysis only over explicitly authorized scope.

## Stack & commands

- Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Postgres 16, MinIO (S3), managed with uv.
- Start infra: `docker compose up -d` (MinIO image is `quay.io/minio/minio` — Docker Hub
  no longer carries it).
- Migrate shared schema: `uv run alembic -n platform upgrade head`
- Migrate all tenant schemas: `uv run python -c "from vista.tenancy import migrate_all_tenants; migrate_all_tenants()"`
- API: `uv run uvicorn vista.main:app --reload` · Worker: `uv run python -m vista.jobs.worker`
- Tests: `uv run pytest` (skip automatically if Postgres unreachable)

## Architecture decisions (locked in)

- **Schema-per-tenant** isolation: shared `platform` schema (tenants, users, jobs) plus
  one `t_<hex>` schema per firm. Tenant sessions set `search_path` in `src/vista/db.py`
  and exclude `platform`.
- **Postgres-backed durable job queue** (`FOR UPDATE SKIP LOCKED`, retries with backoff,
  idempotency keys) — no Redis/Celery for now.
- Deal-level roles: owner > member > viewer. Documents = S3 blobs + Postgres metadata,
  presigned URLs.
- Append-only `agent_runs`/`agent_run_events` as execution log + audit trail; one
  `usage_events` row per model call for cost tracking.
- Agent handler in `src/vista/jobs/handlers.py` is currently a stub (no LLM key needed);
  real model calls slot in there.

## Git

- Push to `origin` (github.com/emiliano800/vista) ONLY. Never push to `upstream`
  (ylemiesa57/vista) unless explicitly asked.

## Gotchas

- Tenant Alembic env (`migrations/tenant/env.py`): do not execute statements after
  `connection.commit()` before `context.configure` — Alembic will assume an external
  transaction and silently roll back migrations.
- API tokens are plaintext in `platform.users` and `POST /tenants` is unauthenticated —
  both must be hardened before deployment.
