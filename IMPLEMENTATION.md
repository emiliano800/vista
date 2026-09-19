# Vista — Milestone 1 Implementation Notes

This document explains what was built in the first backend milestone, how it works,
and includes a primer on how the "agent" machinery functions for readers new to it.

## What was built

Milestone 1 is a small but complete backbone: a multi-tenant FastAPI backend where a
PE firm (tenant) can be provisioned, create deals, register documents, kick off a
durable agent run against a document, and see the run's execution log and cost.

### Stack

| Layer | Choice |
| --- | --- |
| Language / framework | Python 3.12, FastAPI |
| ORM / migrations | SQLAlchemy 2, Alembic |
| Database | Postgres 16 (Docker locally, RDS later) |
| Blob storage | S3 API — MinIO locally (`quay.io/minio/minio`), AWS S3 later |
| Package manager | uv |
| Queue | Postgres itself (no Redis/Celery — see below) |

### 1. Tenant isolation: schema-per-tenant

Every firm gets its **own Postgres schema** (`t_<12 hex chars>`), holding its deals,
documents, agent runs, logs, and usage. A shared `platform` schema holds only what
must be global: the tenant registry, users, and the job queue.

- `POST /tenants` creates the tenant row, runs Alembic migrations to build the new
  schema, and returns an owner user + API token (`src/vista/tenancy.py`).
- Every request authenticates, resolves the caller's tenant, and opens a session
  whose `search_path` is locked to that tenant's schema (`src/vista/db.py`).
  `platform` is deliberately **excluded** from the tenant search_path, so tenant-scoped
  code physically cannot touch shared tables by accident.
- There are two Alembic environments: `alembic -n platform upgrade head` for the
  shared schema, and a tenant environment applied per-schema
  (`vista.tenancy.migrate_all_tenants()` loops over every tenant).

Why this matters for PE: a deal document leaking between firms is an existential
failure. Schema separation means an entire class of "missing WHERE clause" bugs
cannot cross firms.

### 2. Documents and permissions

- Documents belong to **deals**. Users get per-deal roles: `owner` > `member` > `viewer`.
- Uploads/downloads use **presigned S3 URLs** — file bytes never pass through the API
  server; Postgres stores metadata only (`src/vista/storage.py`).
- Every document/run endpoint checks the caller's deal role
  (`src/vista/permissions.py`). Viewers can read, members can upload and start runs.

### 3. Durable jobs (Postgres-backed queue)

Agent work must survive process crashes and be retried. Instead of adding Redis or
Celery, the queue is a `platform.jobs` table (`src/vista/jobs/queue.py`):

- Workers claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED` — a standard Postgres
  pattern that lets many workers pull safely without double-processing.
- Failures are retried with backoff (10s / 60s / 300s) up to `max_attempts`, then
  marked permanently failed.
- **Idempotency keys**: submitting the same (tenant, kind, key) twice returns the
  existing job instead of duplicating work.

The worker (`src/vista/jobs/worker.py`) is a plain loop: claim → dispatch to a
handler → mark succeeded/failed. Run it with `uv run python -m vista.jobs.worker`.

### 4. Agent execution logs

Every run writes an **append-only** event stream in the tenant's schema:
`agent_runs` (status, timestamps) and `agent_run_events` (ordered `seq`, event type,
JSON payload). `GET /runs/{id}` returns the run plus its full event log. This doubles
as the audit trail PE compliance will ask about.

### 5. Cost tracking

Each model call writes a `usage_events` row (model name, input/output tokens,
computed `cost_usd`). `GET /usage` aggregates per tenant. Captured at maximum
granularity now because you cannot reconstruct per-run costs retroactively.

### Tests (all passing against real Postgres)

- `tests/test_tenant_isolation.py` — firm B cannot see firm A's deals, documents, or
  runs; unauthenticated requests rejected. The most important tests in the repo.
- `tests/test_permissions.py` — non-members get 403; viewers can read but not upload.
- `tests/test_jobs.py` — end-to-end run (queued → events → succeeded → usage recorded),
  idempotency dedup, retry-then-permanent-failure.

One real bug was caught during this milestone: the tenant Alembic environment left a
transaction open after `commit()`, causing migrations to silently roll back. Fixed in
`migrations/tenant/env.py` (see the comment there).

---

## Primer: how the "agent stuff" works

If you're new to AI agents, here is the mental model, mapped to this codebase.

### What an agent is

An **agent** is a program that uses a large language model (LLM) in a loop:

1. Give the LLM a goal and some context ("summarize this CIM", "extract vendors
   from these invoices").
2. The LLM responds with either an answer or a request to use a **tool** — a
   function you expose, like `read_document(id)` or `search_records(query)`.
3. Your code executes the tool and feeds the result back to the LLM.
4. Repeat until the LLM produces a final result.

The LLM does the *reasoning*; your application code does the *doing* — and enforces
what is allowed. The agent never touches the database or S3 directly; it can only
call tools you wrote, with the permissions of the run that invoked it.

### Why runs are jobs, not HTTP requests

Agent loops are slow (seconds to minutes), can fail mid-flight (rate limits, model
errors), and cost real money per step. So a run is:

- **Enqueued** as a durable job (`POST /runs` → `platform.jobs`) and processed by a
  background worker, so an API restart loses nothing.
- **Retried** automatically on transient failure, with an idempotency key so a
  double-click doesn't buy two runs.
- **Logged** step by step (`agent_run_events`): every step, tool call, and model call
  becomes an event row. This is how you debug "why did the agent say that?" and how
  you prove to a client what the agent did and did not read.
- **Metered**: every model call has a token count and a price. A `usage_events` row
  per call is the raw material for cost dashboards, per-run cost, and eventually
  billing.

### Where the LLM plugs in

Right now the agent is a **stub**: `handle_agent_run()` in
`src/vista/jobs/handlers.py` emits a realistic event sequence
(`step → tool_call → model_call → result`) with fake token counts. To make it real,
replace the stub `model_call` section with an actual LLM API call (Anthropic/OpenAI
SDK), record the real token usage from the API response, and add real tools. Nothing
else in the system changes — the queue, logs, permissions, and cost tracking are
already built around it.

### The vocabulary in this codebase

| Term | Meaning here |
| --- | --- |
| Run (`agent_runs`) | One agent execution against a deal/document |
| Event (`agent_run_events`) | One step in a run: `step`, `tool_call`, `model_call`, `error`, `result` |
| Job (`platform.jobs`) | The durable queue entry that causes a run to execute |
| Usage event (`usage_events`) | One model call's tokens + cost |
| Tool | A function the agent may call, gated by application code |

---

## About the API tokens

**No external/third-party API keys are used anywhere in this milestone.** Specifically:

- The agent is a stub — there is no OpenAI/Anthropic key. One will be needed when a
  real model call is added.
- The **bearer tokens** used by the tests and API are generated by Vista itself:
  `secrets.token_hex(32)` at tenant provisioning (`src/vista/tenancy.py`), returned
  once by `POST /tenants`, and stored in `platform.users.api_token`.
- The Postgres/MinIO credentials in `.env.example` (`vista` / `vista-secret`) are
  local-development defaults for the Docker containers only.

Hardening needed before any real deployment: hash stored API tokens, gate
`POST /tenants` behind an ops/admin credential, and replace token auth with a real
IdP (SSO) for firm users.
