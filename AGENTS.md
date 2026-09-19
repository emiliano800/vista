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
Docs: IMPLEMENTATION_SO_FAR.md (what's built), FURTHER_STEPS.md (tech roadmap +
frontend spec), BUSINESS_COURSE_OF_ACTION.md (business plan distilled). The original
verbatim business plan (BUSINESS_PLAN_AND_IDEAS.md) and IMPLEMENTATION.md were removed
as superseded; both remain in git history.

Agent end-state (user's vision): one autonomous agent per back-office employee that
knows only the person's role and discovers inefficiencies itself (codex-style, eventually
with scoped computer access); findings roll up into a per-company summary, then a
portfolio summary. Autonomy ladder: records agent (current) → read-only connector agent
(email, QuickBooks) → shadow agent → autonomous execution with review gates. See README
"Agent vision" section.

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

- Remotes: `origin` = github.com/emiliano800/vista (full repo, including docs);
  `upstream` = github.com/ylemiesa57/vista (code only).
- Always pull/fetch both remotes before pushing.
- Push code changes to BOTH remotes. The doc files README.md, IMPLEMENTATION_SO_FAR.md,
  FURTHER_STEPS.md, BUSINESS_COURSE_OF_ACTION.md, and AGENTS.md (any root-level .md)
  must NEVER be committed/pushed to `upstream` — they go to `origin` only.
- Workflow: `main` tracks origin and carries everything. The `upstream-main` branch
  mirrors the code without those doc files and is pushed to `upstream`'s main.
- Never commit `.env` (holds the OpenAI API key; gitignored).

## Gotchas

- Tenant Alembic env (`migrations/tenant/env.py`): do not execute statements after
  `connection.commit()` before `context.configure` — Alembic will assume an external
  transaction and silently roll back migrations.
- Access keys are SHA-256 digests in `platform.users`; browser sessions use HttpOnly
  cookies. Public `POST /tenants` is disabled unless an operator key is configured.
  Provision users with `python -m vista.manage`; keep `VISTA_COOKIE_SECURE=true` on HTTPS.
- Web dashboard: `src/web/public`, Cloudflare proxy: `src/web/worker.mjs`. Recording
  reports require the Python/Postgres/S3 backend; follow `deploy/README.md`.
  Only completed summaries and cleaned activity CSVs upload; raw capture stays local.
- AWS hosting: `deploy/aws/` (CloudFormation: ECS Express Mode on Fargate + RDS Postgres +
  private S3 + optional worker; `deploy.sh` builds/pushes/deploys, `manage.sh` runs
  `vista.manage` as a one-off Fargate task). Cloudflare keeps the site; its Worker's
  `API_ORIGIN` points at the stack's `ApiEndpoint`. The container entrypoint
  (`docker-entrypoint.sh`) migrates on start when `VISTA_MIGRATE_ON_START=true`;
  `VISTA_DB_*` parts compose the database URL and empty S3 endpoint/keys mean IAM-role auth.
