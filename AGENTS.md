# Vista — Agent/Project Context

## What Vista is

Operating intelligence for lower-middle-market private equity. A PE firm gets a
portfolio workspace over its acquired companies; each company is an isolated tenant
workspace; four AI agents (File Reviewer, Sector Merger, Report Generator, Recording
Reviewer) run as durable jobs producing evidence-linked findings, cross-company
opportunities, and reports, with per-call cost tracking. Docs: README.md (entry),
CURRENT_IMPLEMENTATION.md (what exists), NEXT_STEPS.md (roadmap),
BUSINESS_COURSE_OF_ACTION.md (business plan), DESIGN.md (Field Notes design system),
DEMO_ACCESS.md (public demo keys — synthetic data only).

Key product principles:
- Observed facts vs recommendations vs realized results stay distinct; every agent
  finding cites source records (file/column/row provenance).
- Application code validates, calculates, and enforces permissions; the LLM
  interprets and proposes. Agents never change source systems.
- Company data stays separated (schema-per-tenant); portfolio analysis only over
  explicitly authorized scope (platform `firm_companies`).
- User's long-term vision: one autonomous agent per back-office employee that knows
  only the role and discovers inefficiencies itself; autonomy ladder = records agent
  (current) → read-only connectors (email, QuickBooks) → shadowing → scoped execution
  with review gates.

## Stack & commands

- Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Postgres 16, MinIO/S3, uv; Electron
  recorder; Cloudflare Worker + static web; Node 22 for JS tests (installed under
  `~/.local/bin`).
- Start infra: `docker compose up -d` (MinIO image is `quay.io/minio/minio`).
- Migrate everything + bucket: `uv run python -m vista.manage migrate`
- API: `VISTA_COOKIE_SECURE=false uv run uvicorn vista.main:app --reload`
- Worker (agent jobs): `uv run python -m vista.jobs.worker`
- Tests: `uv run pytest` (DB tests skip if Postgres down; live-model tests are
  opt-in via `pytest -m live`) · `npm test` · lint: `uv run ruff check . && uv run
  ruff format --check .` (repo-wide; CI enforces) · `npx wrangler deploy --dry-run`
- Provisioning (operator-only): `python -m vista.manage create-workspace|add-user|
  rotate-key|provision-firm|seed-portfolio-demo`; in AWS via `deploy/aws/manage.sh`
  (prefer `--output-file PATH` so keys skip CloudWatch).
- E2E recipe: `.agents/skills/vista-api-e2e-testing` skill.

## Architecture decisions (locked in)

- **Schema-per-tenant** isolation; shared `platform` schema holds tenants, users,
  browser sessions, jobs, and the firm layer (`firms`, `firm_memberships`,
  `firm_companies`, firm-scoped `opportunities`, portfolio activity).
- **Postgres-backed durable job queue** (`SKIP LOCKED`, retries/backoff, idempotency
  keys); handlers in `src/vista/jobs/handlers.py`; agent phases in `src/vista/agents/`.
- Canonical business records (customers/invoices/vendors/purchases/subscriptions/
  tasks) carry row-level provenance (`data_source_type`, source file, import job,
  `synthetic_demo`).
- Append-only `agent_runs`/`agent_run_events` audit trail; one `usage_events` row
  per model call; model pricing table in `src/vista/agents/runtime.py`.
- Reasoning models (gpt-5+/gpt-6/o-series): use `settings.openai_completion_kwargs`
  / `agents.llm.live_chat` — they reject `max_tokens` and explicit temperature and
  need raised completion budgets. Never reintroduce raw `max_tokens`.

## Git

- Remotes: `origin` = github.com/emiliano800/vista (full repo, including docs;
  **the only sanctioned deploy source**); `upstream` = github.com/ylemiesa57/vista
  (code only; its auto-deploy workflow was removed deliberately — do not restore).
- Always fetch both remotes before pushing. Push code to BOTH; root-level doc .md
  files (README, AGENTS, DESIGN, DEMO_ACCESS, CURRENT_IMPLEMENTATION, NEXT_STEPS,
  BUSINESS_COURSE_OF_ACTION) go to `origin` only — when merging upstream, restore
  any root docs its merges delete.
- Never commit `.env` or `deploy/aws/.env` (hold the OpenAI key; gitignored).

## Live deployment (2026-09-20)

- AWS account 630396228214, us-east-1, CloudFormation stack `vista`: ECS cluster
  `vista` with `vista-api` and `vista-worker` (WorkerDesiredCount=1), model
  **gpt-6-astra** (key in Secrets Manager `vista/openai-api-key`), RDS
  `vista-postgres`, S3 `vista-reports-630396228214`, endpoint
  `https://vi-6526b1efec4446e48c627173e9e805ce.ecs.us-east-1.on.aws`.
- Cloudflare Worker `vista` serves bumpsolutions.org and auto-builds on push
  (`npx wrangler deploy`); it proxies an explicit `/api` allow-list — new API routes
  must be added to `src/web/worker.mjs` or they 404 in production.
- Deploys: `deploy/aws/deploy.sh` needs a deployment-capable identity. The local
  AWS CLI default is the scoped `emiliano-vista-operator` (Keychain): it can run
  manage tasks and read logs but CANNOT deploy (no UpdateStack/ECR push). The user
  runs deploys with their admin credentials.
- Entry `docker-entrypoint.sh` migrates on start (advisory-locked). Never deploy an
  image whose migrations are OLDER than the DB head — startup crashes with
  "Can't locate revision" and wedges the stack in rollback (happened 2026-09-20).
- Live demo data: six "Vista Capital Demo" company workspaces (Meridian, Harborline,
  Castlebrook, Northfield, Keystone, Ridgeway) with recording reports + agent runs;
  Northstar HVAC Holdings analyst firm. Keys are public in DEMO_ACCESS.md
  (synthetic only). CAUTION: the FIRST key in DEMO_ACCESS.md is the Northstar
  ANALYST key, not Meridian — scripts must select keys by section, not position.
- Old "Vista Solutions / Vista Demo" key was revoked 2026-09-19 (rotation,
  replacement destroyed unread).

## Gotchas

- Tenant Alembic env (`migrations/tenant/env.py`): no statements after
  `connection.commit()` before `context.configure` — silent rollback otherwise.
- `ruff format --check` is repo-wide in CI: format new files before pushing.
- The scheduler test iterates ALL tenant schemas — stale local schemas at old
  revisions break it; fix with `migrate_all_tenants()`, not code changes.
- OpenAI key + demo keys have passed through chats/public repo: rotate all of them
  before any real-customer use.
