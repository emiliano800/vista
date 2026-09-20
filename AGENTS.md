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

## Agent catalog

Keys and run-type mapping live in `src/vista/agents/keys.py`; handlers in
`src/vista/jobs/handlers.py`; phase logic in `src/vista/agents/`.

| Agent (`agent_key`) | Run types (`agent_runs.run_type`) | Phase code | Reads | Writes |
| --- | --- | --- | --- | --- |
| **File Reviewer** (`file_reviewer`) | `deal_analysis`, `employee_discovery`, `synthetic_discovery` | `discover.py` | one division's tables (csv/xlsx) + deterministic profile | `findings` kind `observed_fact` (file/column/row refs, confidence) |
| **Sector Merger** (`sector_merger`) | `synthetic_analyze` | `analyze.py` | approved facts + one opportunity kind across sister companies in a sector (only `firm_companies` scope) | `platform.opportunities` → `findings` kind `proposed_automation`; rejected look-alikes logged as `step` events |
| **Pipeline & Report Generator** (`report_generator`) | `company_summary` | handler only | open `findings` for a company | `company_summaries` (verified facts kept separate from hypotheses) |
| **Recording Reviewer** (`recording_reviewer`) | `recording_review` (+ `extract_recording_files`) | handler only | recorder report bundle (cleaned, on-device redacted) | explanations awaiting employee approve/fix/explain; `findings` |

Internal (not user-facing) phases: **Config Proposer** (`propose.py`, facts →
reviewable `ProposeOutput.proposals` (column_mapping / dedupe_merge / rule /
workflow_change; no table yet — consumed in-memory by tests/eval) and
**Division Executor** (`execute.py`, approved proposals → `findings`/`tasks`,
scope-checked). Every phase is `prepare → chat → parse → apply` via
`agents/runtime.run_phase`; only `chat` touches a model.

## A2A (agent-to-agent) protocol

Agents never call each other in-process. All hand-offs go through the platform
job queue and the tenant ledger, so every step is durable, retryable, metered, and
auditable. The rules below are the contract.

**1. Transport — `platform.jobs` only.**
- Agent A hands work to Agent B by `jobs.queue.enqueue(tenant_id, kind, payload,
  idempotency_key)`. `kind` must be a key of `HANDLERS`. Never import another
  agent's handler or phase function to run it inline.
- `idempotency_key` is mandatory for A2A hand-offs and is derived from the
  producing run: `f"{run_type}:{run_id}:{target_kind}[:{shard}]"`. Re-enqueueing is
  a no-op (`UniqueConstraint(tenant_id, kind, idempotency_key)`).
- Payload is JSON, small (ids + scope, never data rows): at minimum
  `{"run_id": <new AgentRun id>, "parent_run_id": <producer run id>, "scope": {...}}`.
  The consumer reads its inputs from the ledger by id, not from the payload.

**2. Envelope — one `AgentRun` per hop.**
- The producer creates the consumer's `AgentRun` row (`status="queued"`,
  `agent_key` set via `agent_key_for(run_type)`, `company`/`division`/`sector`
  dimensions copied from the producer) *before* enqueueing, and records
  `{"event_type": "handoff", "data": {"to": run_type, "run_id": ..., "job_id": ...}}`
  on its own event stream.
- The consumer's first event is `{"event_type": "step", "data": {"message":
  "started", "parent_run_id": ...}}`. Lineage is always recoverable by walking
  `parent_run_id` in event data; never rely on timing.
- Event types are fixed: `step | tool_call | model_call | finding | handoff |
  error | result`. `seq` comes from `_next_seq` so retries never collide.

**3. Messages — ledger rows are the only shared state.**
- Facts flow as `findings` (`kind=observed_fact`), interpretations as `findings`
  (`kind=inefficiency|proposed_automation`) or `platform.opportunities`, reviewable
  config as proposals (table pending), narrative as `company_summaries`. Nothing is passed as
  free text between agents.
- A consumer may only read rows whose `status` is `open`/`reviewed` (findings) or
  approved (proposals). `dismissed` rows are invisible to downstream agents.
- Every produced row cites its inputs: `evidence` carries source refs
  (`file/column/row`) **and**, for derived rows, the upstream ids
  (`{"from_findings": [...], "from_run": ...}`). A row without evidence is rejected
  in `parse`/`apply`, not stored.

**4. Scope — the consumer re-checks, never trusts.**
- Company agents (File Reviewer, Report Generator, Recording Reviewer) run inside
  one tenant schema and cannot be handed cross-tenant scope.
- Sector Merger is the only cross-company agent; its handler re-validates every
  company in `scope` against `platform.firm_companies` for the firm on the run,
  and drops anything outside it with an `error` event rather than failing open.
- Permissions are enforced in `apply` by application code; the model output is
  never the authority on what may be written.

**5. Review gates — humans sit between agents.**
- `observed_fact` → Config Proposer is automatic. Proposals → Division Executor,
  and anything → source systems, require a human `approved` status first.
- Recording Reviewer never hands off below the confidence threshold; it waits for
  the employee.
- An agent may *suggest* the next hop by emitting a `handoff` event with
  `"pending_review": true` and no `job_id`; the API turns that into a real
  enqueue only on approval.

**6. Failure and retry.**
- Consumers are idempotent per `(run_id)`: on retry they resume from `_next_seq`
  and must not duplicate rows (dedupe on evidence refs + kind + title).
- `max_attempts` exhausted → `AgentRun.status="failed"` with `error`; the producer
  is *not* rolled back. A failed hop is surfaced in the parent's trace by the API,
  not by mutating the parent.
- Cost accrues to the run that made the call (`usage_events.run_id`); A2A adds no
  hidden spend.

**7. Versioning.**
- New run types: add to `AGENT_KEY_BY_RUN_TYPE`, `HANDLERS`, the `/api` allow-list
  in `src/web/worker.mjs`, and this table. Payload changes must stay
  backward-readable by in-flight jobs (additive fields only; never rename).

Status today: no handler enqueues another handler yet — every run is started by an
API route (`api/runs.py`, `api/synthetic.py`, `api/summaries.py`, `api/employees.py`,
`api/recordings.py`, which queues `extract_recording_files` on upload and
`explain_recording` on submit). The chain `synthetic_discovery → synthetic_analyze
→ company_summary` is human-driven through those routes; Proposer/Executor are
wired only in tests/eval. Any new automatic hop must follow the contract above.

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
