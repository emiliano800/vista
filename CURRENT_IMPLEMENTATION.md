# Vista — Current Implementation

Everything that exists and works today. (Roadmap: `NEXT_STEPS.md`. Business:
`BUSINESS_COURSE_OF_ACTION.md`.)

## Ingestion workspace rebuild (local; production deployment pending)

The new `/account/` workspace is organized around **Overview → Data sources → Findings**.
It accepts CSV/TSV/XLSX exports, persists original files to S3, previews editable
field mappings, and runs deterministic source-linked commission, receivables and
data-quality checks after confirmation. Imports and review history persist in the
tenant's `import_batches` table (migration 0007). Existing recording reports are
available at `/account/recordings/` as supporting evidence.

The Meridian sample imports five files / 246 records. Its commission check calculates
**$1,584.48** from policy and statement rows. Findings have source evidence, calculation,
recommended next steps and reviewed/dismissed/reopen actions; none imply realized savings.
No model call or worker is needed. Each import is an independent snapshot, not a
cross-import or portfolio analysis. This is a hackathon implementation focused on
insurance exports, not a general-purpose connector platform.

Run `uv run python scripts/prepare_ingestion_demo.py`, then start the API on port
8010 with `VISTA_COOKIE_SECURE=false`. Local credentials and the presentation sequence
are described in [deploy/INGESTION_DEMO.md](deploy/INGESTION_DEMO.md).
**Backend + migration must deploy before the new web frontend.** The older live
backend does not have these ingestion endpoints yet. The sections below describe
the previously deployed capabilities and retain their original deployment context.

## The system at a glance

```
src/recorder/     Electron desktop recorder (capture stays local)
src/taskmining/   On-device task-mining pipeline: capture → redact → sessionize →
                  discover patterns → analytics → report bundle
src/vista/        FastAPI backend: tenancy, deals/documents, employee agents,
                  findings, summaries, durable jobs, recordings API
src/web/          Browser workspace (static UI) + Cloudflare Worker proxy
deploy/           Docker hosting + AWS CloudFormation (ECS Fargate + RDS + S3)
```

**Live deployment (2026-09-19):** https://bumpsolutions.org — Cloudflare Worker
`vista` serves the UI and proxies `/api` to the AWS backend (ECS Express Mode on
Fargate, RDS Postgres, private S3, one CloudFormation stack in us-east-1, account
630396228214). First workspace: firm *Vista Solutions*, company *Vista Demo*. Ops
details and scoped-operator setup live in `AGENTS.md` and `deploy/aws/README.md`.

## Stack

| Layer | Choice |
| --- | --- |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2, Alembic (separate platform/tenant migration envs) |
| Database | Postgres 16 (Docker locally, RDS in prod) |
| Blob storage | S3 API — MinIO locally, private AWS S3 in prod (IAM-role auth) |
| LLM | OpenAI (`gpt-4o-mini` default) when `VISTA_OPENAI_API_KEY` set; deterministic stub otherwise |
| Jobs | Postgres queue (`FOR UPDATE SKIP LOCKED`) — no Redis/Celery |
| Desktop | Electron recorder + local Python task-mining pipeline |
| Web | Static UI served by FastAPI/Cloudflare; Worker proxy (`src/web/worker.mjs`) |
| Tooling | uv, ruff, pytest (55 tests) + JS tests |

## Backend core

**Tenant isolation — schema-per-tenant.** Each company gets its own Postgres schema
(`t_<hex>`); the shared `platform` schema holds only tenants, users, browser
sessions, and the job queue. Tenant sessions set `search_path` in one chokepoint
(`src/vista/db.py`) with `platform` excluded, so tenant code physically cannot cross
firms. Provisioning/migration via `python -m vista.manage` (public `POST /tenants`
is disabled unless an operator provisioning key is configured).

**Auth & security.** Access keys are stored as SHA-256 digests only (write-only
`api_token` property); browser sign-in issues expiring HttpOnly session cookies tied
to the key hash — key rotation invalidates sessions. Cross-origin browser calls
require a custom header (forces CORS preflight) and an allow-listed origin. Request
bodies capped at 8 MiB; security headers on every response; validation errors never
echo credentials.

**Deals & documents.** Documents belong to deals; per-deal roles
(`owner` > `member` > `viewer`) checked on every endpoint. Blobs move via presigned
S3 URLs; Postgres keeps metadata only.

**Durable jobs.** `platform.jobs` with `SKIP LOCKED` claims, retry backoff
(10s/60s/300s), permanent failure after `max_attempts`, idempotency keys. Worker:
`python -m vista.jobs.worker`; recurring discovery via `python -m vista.jobs.scheduler`.

**Audit & cost.** Every agent run writes append-only `agent_runs` +
`agent_run_events`; every model call writes a `usage_events` row (tokens, unit
price, cost). `GET /usage` aggregates per tenant.

## Employee agents, findings, summaries

Level 1 of the agent hierarchy (employee agent → company summary):

- **Registry:** `employees` (name + role title) and `employee_agents` (one per
  employee: scopes, schedule, status). On-demand runs (`POST /agents/{id}/runs`) or
  scheduled (hourly/daily/weekly, idempotent per window).
- **Findings:** each run produces typed, evidence-labeled findings
  (`observed_fact` / `inefficiency` / `proposed_automation`; source, confidence,
  verify_by). With no connectors yet these are explicitly role-based hypotheses.
  Triage: open → reviewed / dismissed / actioned.
- **Company summary:** a run that aggregates all open findings into an LLM-written
  operational brief + stats (`GET /summaries/latest`).
- **Roles:** platform `admin` creates/triggers; `member` views.

## Desktop task mining + recording reports

- The Electron recorder captures desktop activity **locally**; the task-mining
  pipeline (redaction/PII masking, keystroke aggregation, sessionization, pattern
  discovery, analytics) runs on-device. Raw events, screenshots, and video never
  leave the machine.
- Only completed report bundles upload: manifest + summary + cleaned event CSV, into
  private S3 with tenant/company isolation. Uploads are idempotent; re-upload
  updates the visible snapshot (`src/vista/api/recordings.py`, tenant migration 0003).
- The current browser workspace lets a signed-in user pick an assigned company,
  review recommendations against individual source rows, and export reports. The UI
  labels on-device analysis and distinguishes candidate activity hours from realized
  savings. **Note: this report-viewer UI is a stopgap, not the target product — see
  `NEXT_STEPS.md` §1.**

## API surface

| Endpoint | Purpose |
| --- | --- |
| `POST /tenants` | Operator-only (provisioning key); prefer `vista.manage create-workspace` |
| `POST/GET /deals`, `.../documents` | Deals + presigned document upload/download, role-gated |
| `POST /runs`, `GET /runs/{id}` | Durable runs; status + full event log |
| `POST/GET /employees`, `POST/GET/PATCH /agents`, `POST /agents/{id}/runs` | Employee-agent registry + discovery |
| `GET/PATCH /findings` | Evidence-labeled findings + triage |
| `POST/GET /summaries`, `GET /summaries/latest` | Company summary runs |
| `GET /usage` | Token/cost totals |
| sessions + recordings routes | Browser sign-in/out; report upload/list/download |

All routes also served under `/api` for the Worker proxy.

## How the agents work (primer)

An agent = an LLM in a loop: goal + context in; either an answer or a tool request
out; application code executes tools, enforces permissions, feeds results back. The
LLM reasons, the app acts. Runs are durable **jobs**, not HTTP requests: the queue
gives crash-survival and retries; events give auditability; usage rows give per-run
cost. Model calls are isolated in `src/vista/jobs/handlers.py`.

## Tests (55 Python + JS suite)

Tenant isolation, deal permissions, job durability/idempotency, employee-agent
discovery/triage/summary/scheduler, config composition (RDS parts, IAM-role S3),
task-mining pipeline (redaction, sessionization, discovery), web/session flows, and
AWS manage-script safety (key output, launch failures, no file overwrite). Tests
force the stub model (no API spend) and skip DB suites if Postgres is down.

## Running locally

```sh
docker compose up -d
uv sync && cp .env.example .env
uv run python -m vista.manage migrate              # shared + tenant schemas + bucket
VISTA_COOKIE_SECURE=false uv run uvicorn vista.main:app --reload
uv run python -m vista.jobs.worker                 # separate terminal
uv run pytest
```

Hosting: `deploy/README.md` (Docker/Caddy) and `deploy/aws/README.md`
(CloudFormation; `deploy.sh` to deploy, `manage.sh` for one-off management tasks).

## Known limits

- Desktop reports are **not yet inputs** to employee-agent findings or company
  summaries — the two pipelines are parallel, not joined.
- Report scores are locally computed review candidates, not proven savings; the
  backend validates structure/evidence links, not every metric.
- Findings without connectors remain role-based hypotheses.
- Public auth still needs rate limiting + monitoring before broad customer use;
  workforce SSO and a dedicated deployment role are pending.
- Real employee capture (permissions, redaction in the wild, consent/retention) has
  not been certified — the live roundtrip used synthetic events.
- Migration gotcha: in `migrations/tenant/env.py`, never execute statements after
  `connection.commit()` before `context.configure` (Alembic silently rolls back).
