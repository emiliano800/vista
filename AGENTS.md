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

## Product direction — 2026-09-23

Vista is the AI operating platform for PE rollups, delivered as a service: Vista
connects an acquired company's systems, records employee workflows, builds and
deploys agents to run them, and monitors exceptions over time. "AI services on the
outside, software platform underneath" — not a consultancy. The feature test is
whether a change reduces Vista human labor for the next deployment. Details in
BUSINESS_COURSE_OF_ACTION.md; roadmap ordering in NEXT_STEPS.md.

One platform, three user views:

- **Reporting / PE analyst:** financial performance, model inputs and
  assumptions, opportunities, and validated impact across explicitly authorized
  portfolio companies. This is the visibility layer the service delivers.
- **Reporting / portco CFO:** the assigned company's subset of the same
  analyst financial model, metric definitions, periods, and evidence. No sibling
  company data or portfolio-wide comparisons. Visibility is a subset; write and
  approval permissions must be defined independently rather than assuming read-only.
- **Deployment / FDE (forward-deployed engineer):** Vista's own delivery surface —
  assigned workflows, automation proposals/configuration, testing, approvals,
  execution status, exceptions, and measured operational outcomes. FDE access is a
  separate scope, not inherited analyst access to the full portfolio's financial data.

Keep shared canonical data and provenance behind these views. Link FDE outcomes to
financial implications with explicit baselines and assumptions; observed facts,
modeled benefits, and realized results stay distinct. Employee recording and
verification supply evidence, rather than forming a fourth management platform.
The split is a product boundary, not a mandate for separate backend deployments.

Implementation status: `src/web/public/portfolio/` and `company/` provide the analyst
foundation; `company/` calls the analyst shell and receives the firm-wide snapshot.
`account/` is the existing company operations/evidence workspace; it imports through
the same canonical contract and reads the same findings, runs and recorder reports
as the analyst (see "One ledger" under Architecture decisions). Dedicated CFO/FDE
roles and views are not implemented. Current firm roles are
`analyst/operator/admin/viewer`; never relabel `operator` as an FDE role without
implementing its scope. Enforce company/workflow restrictions server-side before
exposing new views, including evidence, exports, and job/run endpoints. A financial
metrics dashboard is not yet a full forecasting/valuation model, and an agent run
is not proof of a deployed workflow automation.

## Agent catalog

Keys and run-type mapping live in `src/vista/agents/keys.py`; handlers in
`src/vista/jobs/handlers.py`; phase logic in `src/vista/agents/`.

| Agent (`agent_key`) | Run types (`agent_runs.run_type`) | Phase code | Reads | Writes |
| --- | --- | --- | --- | --- |
| **File Reviewer** (`file_reviewer`) | `deal_analysis`, `employee_discovery`, `synthetic_discovery`, `canonical_review` | `discover.py`; `portfolio/interpret.py` for `canonical_review` | one division's tables (csv/xlsx) + deterministic profile; `canonical_review` reads only the tenant's canonical rows (customers, invoices, vendors, policies, purchase orders, inventory…) | `findings` kind `observed_fact` (file/column/row refs, confidence); `canonical_review` also `tasks`, `company_summaries`, and cites canonical record ids |
| **Sector Merger** (`sector_merger`) | `synthetic_analyze`, `portfolio_merge` | `analyze.py`; `portfolio/interpret.py` for `portfolio_merge` | approved facts + one opportunity kind across sister companies in a sector (only `firm_companies` scope); `portfolio_merge` reads canonical rows plus structured findings of the successful `canonical_review` runs in its `successful_run_ids` | `platform.opportunities` (with `lineage`: `from_findings`/`from_runs`) → `findings` kind `proposed_automation`; rejected look-alikes logged as `step` events |
| **Pipeline & Report Generator** (`report_generator`) | `company_summary` | handler only | open `findings` for a company | `company_summaries` (verified facts kept separate from hypotheses) |
| **Recording Reviewer** (`recording_reviewer`) | `recording_review` (+ `extract_recording_files`), `submission_analysis` (job `analyze_submission`) | handler only; `recorder_analysis.py` for `submission_analysis` | v1: recorder report bundle (cleaned, on-device redacted); v2: the accepted `recorder_submissions` package read back from S3 (metadata-only activity + shared documents) | v1: explanations awaiting employee approve/fix/explain; v2: one `recorder_reports` draft (observed facts computed in code, model interpretation kept apart, employee questions) that only the employee can publish |

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

**Two-layer pipeline over `synthetic_data/`.** Layer 1 (facts) is the import
contract (`portfolio/imports.py` + `processors.py`; deterministic header aliases
first, model proposals only for unresolved columns and never auto-confirmed):
every canonical row carries `record_provenance` (file/sheet/row/original/normalized).
`scripts/load_synthetic_portfolio.py` drives it for the six canonical companies.
Layer 2 (interpretation) is `POST /api/portfolio/interpretation` →
`portfolio/interpret.run_portfolio_interpretation`, a hybrid of deterministic
screening and A2A: `canonical_review` and deterministic portfolio screening run
over the same canonical state. Once all company review runs in a sector reach a
terminal state (succeeded or permanently failed — the barrier is
`release_sector_barrier`, run from the worker's after-terminal hook and re-tried
from the status route), a `portfolio_merge` run is queued with
`successful_company_ids`, `failed_company_ids`, `successful_run_ids`, `request_id`,
`sector`. The merger recomputes deterministic cross-company candidates from
canonical state and consumes findings from successful reviewer runs to validate,
suppress, downgrade, or enrich those candidates: reviewers write structured
findings (`finding_type`, `affected_entities`, `severity`, `effect`
`block|degrade|enrich`, `blocking`), and the merger intersects
`affected_entities[].id` with each candidate's canonical `record_ids` — no prose
interpretation decides evidence validity. Failed reviewer scopes are excluded and
recorded explicitly in the merge trace (`error` events, `excluded_company_ids`).
Opportunities carry `lineage` = `{evidence record refs, from_findings, from_runs,
merge_run_id, request_id, effect}`. Stable keys are
`f"{kind}:{request_id}:{scope}"`; the merge `AgentRun` envelope exists from
request time (status `waiting` in the API until its job is created). The analyst "Run portfolio analysis" button calls this
route and polls `GET /api/portfolio/interpretation/{request_id}` until every hop
is terminal (`store.runPortfolioInterpretation`); `POST /api/portfolio/analysis`
is the legacy deterministic SKU-price rule and is no longer wired to the UI.
Layer 2 reads canonical rows by id and never writes to
Layer 1 tables. The legacy `seed.js → portfolio_demo/portfolio.json` fixture is
not a source for the six-company workspace.

Status today: apart from the review → merge barrier above, no handler enqueues another — every run is started by an
API route (`api/runs.py`, `api/synthetic.py`, `api/summaries.py`, `api/employees.py`,
`api/company_imports.py`, which queues a single-company `canonical_review` from the
company workspace (no merge waits on it),
`api/recordings.py`, which queues `extract_recording_files` on upload and
`explain_recording` on submit, and `api/recorder.py`, which queues `analyze_submission`
automatically when a v2 upload is accepted — `recorder_uploads.queue_analysis` creates
the `AgentRun` envelope first, then the job). The chain `synthetic_discovery → synthetic_analyze
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
- Provisioning (operator-only): `python -m vista.manage migrate|create-workspace|
  add-user|rotate-key|seed-portfolio|load-synthetic`; in AWS via `deploy/aws/manage.sh`
  (prefer `--output-file PATH` so keys skip CloudWatch).
- E2E recipe: `.agents/skills/vista-api-e2e-testing` skill.

## Architecture decisions (locked in)

- **Schema-per-tenant** isolation; shared `platform` schema holds tenants, users,
  browser sessions, jobs, and the firm layer (`firms`, `firm_memberships`,
  `firm_companies` (with `deal_id`, the Deal inside the company tenant that
  deal-scoped surfaces use), firm-scoped `opportunities`, portfolio activity).
- **One ledger under every view.** `findings` / `agent_runs` / `agent_run_events` /
  `usage_events` / `company_summaries` are the only agent ledger; the analyst
  workspace derives its agents, runs and findings from them (`portfolio/ledger.py`)
  and never keeps a mirror. Imports go through the canonical contract
  (`portfolio/imports.py`) from the analyst (`api/portfolio.py`, firm scope) and
  from the company workspace (`api/company_imports.py`, deal scope) alike. Never
  add a per-surface copy of findings, runs or imported records.
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

## Git and repository scope

- `origin` = github.com/emiliano800/vista, branch `main`, is the sole source of
  truth for code, documentation, and deployments. Work with this repository only.
- Do not use `ylemiesa57/vista` or `samueljchen08/vista` (samchen), including the
  legacy remote names `upstream` and `sam`. Do not fetch, pull, merge, mirror,
  synchronize, push, or deploy from them, or recreate their checkouts.
- Previous cross-repository sync and documentation-splitting instructions are
  obsolete. All project code and documentation belong in `emiliano800/vista`.
- Do not recreate retired remotes or mirror branches, and do not bring those
  repositories up unprompted. Refer to this project and `origin` going forward.
- Before pushing, verify `origin` points to `emiliano800/vista` and pull
  `origin/main` without overwriting local work. Never fetch or push all remotes.
  Preserve unrelated user edits and never force-push.
- Never commit secrets, `.env`, `deploy/aws/.env`, or credential-output files.

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
- Not yet deployed as of 2026-09-23: the ledger / company-import commits
  (platform migration `0005`, tenant `0020`–`0021`, new `/api/deals/{deal}/imports…`
  and `/api/companies/{id}/reports` routes). Deploy the image and the Worker together.

## Gotchas

- Tenant Alembic env (`migrations/tenant/env.py`): no statements after
  `connection.commit()` before `context.configure` — silent rollback otherwise.
- `ruff format --check` is repo-wide in CI: format new files before pushing.
- The scheduler test iterates ALL tenant schemas — stale local schemas at old
  revisions break it; fix with `migrate_all_tenants()`, not code changes.
- OpenAI key + demo keys have passed through chats/public repo: rotate all of them
  before any real-customer use.
