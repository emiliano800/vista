# Vista — Implementation So Far

What exists in the backend today, how it works, and why it was built this way.
(Roadmap: `FURTHER_STEPS.md`. Business context: `BUSINESS_COURSE_OF_ACTION.md`.)

## Recording web workspace (September 2026)

A small working browser UI now lives in `src/web/public`, backed by the FastAPI report
endpoints and private S3 bundles. Electron's Cloud workspace settings connect with an
OS-encrypted personal key; manual Upload report sends only a completed manifest,
summary and cleaned event log. Upload retries are idempotent, and reuploading updates
the visible snapshot. Browser users sign in through an expiring HttpOnly session,
select an assigned company, inspect recommendations against individual source rows and
export reports. The UI labels on-device analysis and distinguishes candidate activity
hours from realized savings.

Platform migration 0002 hashes existing bearer keys and adds browser sessions; tenant
migration 0003 adds recording metadata. Public tenant provisioning is disabled by
default. `vista.manage` supplies operator provisioning and key rotation; rotations
invalidate browser sessions. Deployment and operating instructions are in
[deploy/README.md](deploy/README.md); the AWS path (ECS Express Mode on Fargate, RDS
PostgreSQL, private S3, all in one CloudFormation stack) is in
[deploy/aws/README.md](deploy/aws/README.md). The Cloudflare Worker keeps serving the site
and proxies `/api` to the AWS endpoint.

## Stack

| Layer | Choice |
| --- | --- |
| Language / framework | Python 3.12, FastAPI |
| ORM / migrations | SQLAlchemy 2, Alembic (separate platform + tenant environments) |
| Database | Postgres 16 (Docker locally; RDS later) |
| Blob storage | S3 API — MinIO locally (`quay.io/minio/minio`), AWS S3 later |
| LLM | OpenAI (`gpt-4o-mini` default) when `VISTA_OPENAI_API_KEY` is set; deterministic stub otherwise |
| Queue | Postgres itself (`FOR UPDATE SKIP LOCKED`) — no Redis/Celery |
| Package manager | uv |

## Milestone 1 — multi-tenant core

**Tenant isolation: schema-per-tenant.** Every firm/company gets its own Postgres
schema (`t_<hex>`). A shared `platform` schema holds only the tenant registry, users,
and the job queue. All tenant sessions are scoped via `search_path` in one chokepoint
(`src/vista/db.py`), with `platform` deliberately excluded so tenant code physically
cannot touch shared tables. `POST /tenants` provisions the row, migrates the new
schema to head, and returns an owner user + API token (shown once).

**Documents & permissions.** Documents belong to deals; users hold per-deal roles
(`owner` > `member` > `viewer`) checked on every endpoint (`src/vista/permissions.py`).
Blobs move via presigned S3 URLs; Postgres stores metadata only.

**Durable jobs.** `platform.jobs` + `SKIP LOCKED` claims, retries with backoff
(10s/60s/300s), permanent failure after `max_attempts`, and idempotency keys that
dedupe repeat submissions (`src/vista/jobs/queue.py`). Worker:
`uv run python -m vista.jobs.worker`.

**Execution logs & cost.** Every run writes append-only `agent_runs` +
`agent_run_events` (ordered seq; step/tool_call/model_call/finding/error/result) —
this is the audit trail. Every model call writes a `usage_events` row (tokens, unit
price, cost); `GET /usage` aggregates per tenant.

## Milestone 2 — employee-agent registry, findings, company summaries

Implements level 1 of the agent hierarchy (employee agent → company summary):

- **Registry**: `employees` (name + role title — all the agent needs to know) and
  `employee_agents` (one per employee: scopes, schedule, status, last_run_at).
- **Discovery runs**: on-demand (`POST /agents/{id}/runs`) or scheduled
  (`python -m vista.jobs.scheduler`, hourly/daily/weekly, idempotent per window).
  The agent produces 2–4 **findings**, each typed
  (`observed_fact` / `inefficiency` / `proposed_automation`) and evidence-labeled
  (source, confidence, verify_by). With no connectors yet, the LLM is explicitly told
  everything is a hypothesis to verify — the evidence labels say so.
- **Triage**: `GET /findings` (filter by status/employee), `PATCH /findings/{id}`
  moving open → reviewed / dismissed / actioned.
- **Company summary**: `POST /summaries` runs a job that reads all open findings and
  writes an LLM-generated operational brief + stats (`open_findings`, `by_kind`,
  `employees_covered`). `GET /summaries/latest` for dashboards.
- **Roles**: platform-level `admin` (create/trigger) vs `member` (view).

## API surface (stable, frontend-ready)

| Endpoint | Purpose |
| --- | --- |
| `POST /tenants` | Provision firm: schema + owner + API token (⚠ unauthenticated; gate before deploy) |
| `POST/GET /deals`, `POST/GET /deals/{id}/documents`, `GET /documents/{id}` | Deals + presigned document upload/download, role-gated |
| `POST /runs`, `GET /runs/{id}` | Deal-analysis runs; status + full event log |
| `POST/GET /employees` | Employee registry |
| `POST/GET/PATCH /agents`, `POST /agents/{id}/runs` | Per-employee agents; trigger discovery |
| `GET/PATCH /findings` | Evidence-labeled findings + triage |
| `POST/GET /summaries`, `GET /summaries/latest` | Company summary runs |
| `GET /usage` | Token/cost totals |

## How agents work here (primer)

An agent = an LLM in a loop: goal + context in, either an answer or a **tool request**
out; application code executes tools, enforces permissions, and feeds results back.
The LLM reasons; the app does — and decides what's allowed. Runs are **jobs**, not
HTTP requests, because agent loops are slow, fail midway, and cost money: the queue
gives crash-survival and retries, events give debuggability and auditability, usage
rows give per-run cost. Model calls live in `src/vista/jobs/handlers.py`
(`_call_model` / `_chat`); everything else is model-agnostic.

## Tests (17 passing)

- **Tenant isolation** — firm B cannot see firm A's deals/documents/runs/employees/findings.
- **Permissions** — non-members 403; viewers read-only; non-admins can't manage employees.
- **Job durability** — end-to-end run, idempotency dedup, retry-then-permanent-failure.
- **Employee agents** — discovery creates findings, triage works, summary aggregates,
  scheduler enqueues once per window.
- Tests force the stub model — they never spend API credits — and skip if Postgres is down.

## Running it

```sh
docker compose up -d
uv sync && cp .env.example .env        # add VISTA_OPENAI_API_KEY for real LLM calls
uv run alembic -n platform upgrade head
uv run uvicorn vista.main:app --reload
uv run python -m vista.jobs.worker      # separate terminal
uv run python -m vista.jobs.scheduler   # optional, for recurring discovery
uv run python scripts/demo.py           # end-to-end smoke test
```

After adding a tenant migration:
`uv run python -c "from vista.tenancy import migrate_all_tenants; migrate_all_tenants()"`

## Known gaps / hardening needed before deployment

- `POST /tenants` is unauthenticated; API tokens stored plaintext (hash them; move to real IdP/SSO).
- Agent findings are role-hypotheses until connectors land (Phase 2 in `FURTHER_STEPS.md`).
- Gotcha for future migrations: in `migrations/tenant/env.py`, never execute statements
  after `connection.commit()` before `context.configure` — Alembic will assume an
  external transaction and silently roll back.
