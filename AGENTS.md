# Vista — Agent/Project Context

## What Vista is

Operating intelligence for lower-middle-market private equity. A PE firm gets a
portfolio workspace over its acquired companies; each company is an isolated tenant
workspace; five AI agents (File Reviewer, Sector Merger, Report Generator, Recording
Reviewer, Computer Use Agent) run as durable jobs producing evidence-linked findings,
cross-company opportunities, reports and — for approved sandbox workflows — bounded,
verified execution, with per-call cost tracking. Docs: README.md (entry),
CURRENT_IMPLEMENTATION.md (what exists), NEXT_STEPS.md (roadmap),
BUSINESS_COURSE_OF_ACTION.md (business plan), DESIGN.md (Field Notes design system),
DEMO_ACCESS.md (public demo keys — synthetic data only).

Key product principles:
- Observed facts vs recommendations vs realized results stay distinct; every agent
  finding cites source records (file/column/row provenance).
- Application code validates, calculates, and enforces permissions; the LLM
  interprets and proposes. Agents never change source systems; the Computer Use
  Agent acts only in the sandbox, on an approved version, behind review gates.
- Company data stays separated (schema-per-tenant); portfolio analysis only over
  explicitly authorized scope (platform `firm_companies`).
- User's long-term vision: one autonomous agent per back-office employee that knows
  only the role and discovers inefficiencies itself; autonomy ladder = records agent
  (current) → read-only connectors (email, QuickBooks) → shadowing → scoped execution
  with review gates. The last rung exists as the Computer Use Agent (bounded, sandbox
  only); real-system connectors are still deferred.

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
roles are not implemented. Current firm roles are
`analyst/operator/admin/viewer`; never relabel `operator` as an FDE role without
implementing its scope. The FDE *clicks* (tier promotion past `ask`, accepting a shadow
disagreement into a draft) exist as a provisional scope, `FirmContext.is_fde`
(`portfolio/access.py`, `FDE_ROLES = {"admin"}`), derived server-side from the firm
membership — never from the request body — and surfaced only on the firm routes
(`/api/companies/{id}/workflows/…/graph`, `/deployment/` page). Tenant workflow routes
always pass `fde=False`. Tenant (company workspace) roles are `admin/member/viewer`
(`tenancy.py`); `owner` is a *deal-membership* role, not a tenant role — the workspace
UI gates on the deal role while the API enforces tenant `admin` for workflow decisions
and runs (`api/company_workflows.py`, `api/computer_use.py`). Enforce company/workflow restrictions server-side before
exposing new views, including evidence, exports, and job/run endpoints. A financial
metrics dashboard is not yet a full forecasting/valuation model, and an agent run
is not proof of a deployed workflow automation.

## Agent catalog

Keys and run-type mapping live in `src/vista/agents/keys.py`; handlers in
`src/vista/jobs/handlers.py`; phase logic in `src/vista/agents/`, except the Computer
Use Agent, which is its own package at `src/vista/computer_use/`.

| Agent (`agent_key`) | Run types (`agent_runs.run_type`) | Phase code | Reads | Writes |
| --- | --- | --- | --- | --- |
| **File Reviewer** (`file_reviewer`) | `deal_analysis`, `employee_discovery`, `synthetic_discovery`, `canonical_review` | `discover.py` for the discovery run types; `portfolio/interpret.py` for `canonical_review`; `deal_analysis` (the default run type of `POST /api/runs`) is still the stub `handle_agent_run` — one `_call_model` over the document, no phase | one division's tables (csv/xlsx) + deterministic profile; `canonical_review` reads only the tenant's canonical rows (customers, invoices, vendors, policies, purchase orders, inventory…) | `findings` kind `observed_fact` (file/column/row refs, confidence); `canonical_review` also `tasks`, `company_summaries`, and cites canonical record ids |
| **Sector Merger** (`sector_merger`) | `synthetic_analyze`, `portfolio_merge` | `analyze.py`; `portfolio/interpret.py` for `portfolio_merge` | approved facts + one opportunity kind across sister companies in a sector (only `firm_companies` scope); `portfolio_merge` reads canonical rows plus structured findings of the successful `canonical_review` runs in its `successful_run_ids` | `platform.opportunities` (with `lineage`: `from_findings`/`from_runs`) → `findings` kind `proposed_automation`; rejected look-alikes logged as `step` events |
| **Pipeline & Report Generator** (`report_generator`) | `company_summary` | handler only | open `findings` for a company | `company_summaries` (verified facts kept separate from hypotheses) |
| **Recording Reviewer** (`recording_reviewer`) | `recording_review` (+ `extract_recording_files`), `submission_analysis` (job `analyze_submission`) | handler only; `recorder_analysis.py` for `submission_analysis` | v1: recorder report bundle (cleaned, on-device redacted); v2: the accepted `recorder_submissions` package read back from S3 — activity events + shared documents; under `sharing_policy` `activity-full-v1` (the recorder's default since 0.5.0, chosen per upload in the dialog) the events also carry window titles, page URLs, control labels, typed text, clipboard contents and opened file names, and `observe` keeps per-app `titles`/`pages`/`files`/`typed` and per-transfer `samples` that Jev and the workspace see; under `activity-metadata-v1` none of that is accepted (422) and the device-side leakage test runs; a shared plan's `type_value` edges carry the run's key script `keys: [{t, key, masked?}]` (every key, ms from the run's first key, `•` when masked; ≤ 2000 per edge, outside node/edge identity and the structural hash) only under `activity-full-v1` — the device strips it otherwise and the cloud refuses a metadata-only plan that carries one; the manifest's `summary_text` (≤ 4096) reaches Jev as `employee_summary` context (≤ 2000), never a candidate or graph field, and Jev-facing facts carry `typing_runs`/`interactions`, not raw key counts | v1: explanations awaiting employee approve/fix/explain; v2: one `recorder_reports` draft (observed facts computed in code; workflow candidates derived from the transfers/loops/stretches in those facts and judged by Jev — `VISTA_RECORDER_INTERPRETER=jev`, the default — or interpreted by the chat model with `=chat`; employee questions) that only the employee can publish. On publish, each judged workflow becomes a `findings` row — kind `proposed_automation` when Jev scored it mechanical enough, else `inefficiency` — citing `report:<id>`, `candidate:<cN>`, `run:<id>`, and carrying the employee's answer plus `actions`: a numbered FDE checklist and, for automation candidates, a prefilled `WorkflowDefinition` (`recorder_uploads.record_findings`, deduped per run) |
| **Computer Use Agent** (`computer_use`) | `workflow_execution` (job `execute_workflow`) | `computer_use/handler.py` (loop), `planner.py` (Jev judgments), `harness*.py` (documents / http / workspace locally; browser / desktop through the employee's recorder), `tools.py` (label → primitives → harness kinds) | one *approved* `workflow_versions` row pinned by `definition_hash`, its bound inputs (documents from accepted submissions, canonical records, declared values), and the candidates each harness enumerates (accessibility-tree controls, document rows, declared input names, allow-listed endpoints) | `workflow_runs` (mutable header: status, checkpoint, limits, lease), `harness_sessions`/`harness_steps`/`harness_devices`, one `tool_call` event per step and one `usage_events` row per judgment on its `AgentRun`, an approval `Task` when paused, and exactly one `findings` row kind `observed_fact` / `finding_type=workflow.execution` with the verification outcome, steps, cost and undo hints |

Internal (not user-facing) phases: **Config Proposer** (`propose.py`, facts →
reviewable `ProposeOutput.proposals` (column_mapping / dedupe_merge / rule /
workflow_change; no table yet — consumed in-memory by tests/eval) and
**Division Executor** (`execute.py`, approved proposals → `findings`/`tasks`,
scope-checked). Every phase is `prepare → chat → parse → apply` via
`agents/runtime.run_phase`; only `chat` touches a model — or `agents/jev.judge`,
the one other function that does. `judge` asks TypeSafe Jev (System One) for *typed*
judgments: code enumerates the facts and the candidates, the model only selects and
grades among them (a probability, a label with its distribution, a position on an
ordered rubric), so nothing it returns can name an app or record code did not put in
front of it and `parse` is trivial. It has the same stub / cassette (`VISTA_JEV_CASSETTE`)
/ live modes as `chat`, is metered exactly like it (one `usage_events` row, `jev` pricing
in `runtime.py`, input tokens only), and never generates prose — templated wording and
every threshold stay in code, so a policy change never re-runs inference.

### Computer Use Agent — bounded, not arbitrary

The fifth agent is the only one that *acts*. It is computer use in the sense of today's
CUA loops (observe → decide → act → verify) but deliberately bounded; the contract is:

1. **Closed action vocabulary.** `navigate, click, type_value, press, read, extract,
   http_get, submit, create_task, screenshot, wait, done, ask_human, none`
   (`computer_use/harness.py`). No scripts, no shell, no free-form tool arguments.
2. **Targets only from code-enumerated candidates.** Every step is one `judge` call
   whose `target` is a `choice` over the ≤40 candidates the harness enumerated
   (accessibility-tree controls, document rows, endpoints); the model cannot name an
   element it was not shown.
3. **Typed values only from declared inputs.** `type_value` picks an input *name*; the
   value is resolved by code. The ledger and every remote request record the name,
   never the value.
4. **Sandbox only.** `environment == "sandbox"` is an eligibility rule; browser steps run
   in the recorder's own partition by default (opt-in `VISTA_CU_BROWSER=chrome` drives a
   tab in the employee's own Chrome over its DevTools port instead — same vocabulary,
   candidates and gates, but the employee's profile), desktop steps refuse
   private/sign-in windows.
5. **Limits enforced per step in code** — `max_steps`, `max_runtime_seconds`,
   `max_cost_usd` (summed from `usage_events`) — and mirrored on the device.
6. **Risk gate.** `submit` always pauses; any primitive with `p_irreversible ≥`
   `VISTA_COMPUTER_USE_RISK_THRESHOLD` (0.3) pauses; two consecutive `none`s pause.
   Paused runs are `waiting_for_human` with an approval `Task`; only tenant `admin`
   decides, per step, by `step_id`.
7. **Independent verification.** A separate `judge` call over the *final observation
   and the success criteria only* (no plan history) decides `succeeded`/`failed`.
8. **Kill switch and lease.** Stop from the workspace or the recorder (⌘⇧Esc) at any
   time; a run-level lease (`lease_owner/lease_until`) stops two workers from acting.
9. **Everything on the ledger.** Observe/act as `tool_call`, judgments as `model_call`
   + `usage_events`, pauses as `step` + `handoff`, the outcome as `finding` + `result`.
10. **Recorded moves only, when a graph exists.** A definition that carries a `PlanGraph`
   (compiled on-device from recordings, `taskmining/state.py` + `recorder/src/plan.js`)
   is planned by `computer_use/graph.py` instead of `planner.py`: code locates the run on
   the graph (`node`), Jev picks among that state's *observed outgoing edges* (`edge`),
   the edge fixes primitive/control/slot, a declared-input slot is resolved without
   asking, a fact slot only from the step whose edge produced it, and an edge's
   `policy` (`confirm`/`always_ask`) gates like the risk gate. If Jev locates the run on
   one state but picks a move recorded from another, it is asked once more over the
   located state's own moves (`rejudged`); `done` is offered only at a state the recordings ended in; no edge fits → `off_plan` pause. Each run returns a `graph_delta` (executed/verified_ok/approved/denied/
   effect_missing, provenance `run:<id>`) for a *draft*; the approved graph is immutable.

Stub Jev (no `VISTA_TYPESAFE_API_KEY`) answers `none` → the agent executes nothing and
pauses at step 1. That is the safe default, not a bug.

Performance numbers come only from `make cu-eval SET=dev|test` (`scripts/cu_eval.py`,
`taskmining/evaluate.py`) over `tests/fixtures/cu/`: recording-compiled graphs only, one
report per git revision (never combined across revisions), `kind: synthetic` cases scored
as smoke and excluded from benchmark totals, the `test` set frozen by manifest hash. It
also reports milestone 1 / 2 status; every threshold constant in `taskmining/` stays
provisional until that report replaces it.

Employee side: the worker cannot reach a laptop, so the recorder *pulls* browser and
desktop steps — presence + offers on its 30 s tick, claim with explicit consent
(`consent.version = computer-use-v1`), 3 s session poll as heartbeat, one result per
step, stop. Drivers (`src/recorder/src/computer-use/`): **browser** = a visible
Electron `BrowserWindow` in its own partition (`persist:vista-computer-use`) driven over
CDP, or with `VISTA_CU_BROWSER=chrome` a new tab in the employee's running Chrome
(`browser-chrome.js`, `VISTA_CU_CHROME_ENDPOINT`, default `http://127.0.0.1:9222`) —
candidates come from the accessibility tree (≤40 named controls; unnamed table rows are
named in code from their first cells), clicks/typing go
through CDP input or the real pointer (`@nut-tree-fork/nut-js`, optional; it jumps to the
control, no glide); **desktop** = frontmost-window accessibility tree (macOS System Events,
or Linux AT-SPI2 via `desktop-linux-atspi.py` + xdotool) + nut-js pointer/keyboard, `null`
elsewhere; `open_app` only raises a window that is already open. Every targeted step cites the observation it was chosen from; stale
observation, changed front window, secure fields and private/sign-in/payment windows
fail closed (`stale_observation` / `sensitive_window`). A computer advertises a harness
only when its driver exists; otherwise `harness_unsupported` pauses the run for a person
(`harnesses.js`). `npm run test:browser` is the real-Electron smoke for the browser driver.

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
  the employee. Its findings exist only after the employee publishes; the workspace's
  **Draft workflow** button then creates a *draft* version through `api/company_workflows.py`
  (tenant-scoped twin of `api/workflows.py`: `member`/`owner` draft, `owner` decides),
  and a draft still needs a decision before it is eligible. An approved sandbox version
  can then be run by the Computer Use Agent (`POST /workflows/{w}/versions/{v}/runs`,
  tenant `admin`), which adds two more gates: the *employee's consent* in the recorder
  before any browser/desktop step, and the *risk gate* (every `submit`, anything judged
  irreversible) that parks the run in `waiting_for_human` until the admin approves that
  exact step.
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
- The Computer Use Agent is the first handler that *suspends*: when a step needs the
  recorder or a human it checkpoints, releases its lease and returns; the API enqueues
  the resume job with a per-step idempotency key (`workflow_run:{id}:resume:{seq}`,
  `…:decision:{seq}:{decision}`, `…:stop`) and the handler files a watchdog job
  (`…:watchdog:{seq}`, `run_at` = step expiry) so an unanswered step expires instead
  of hanging. `queue.enqueue(..., run_at=)` is additive.

**7. Versioning.**
- New run types: add to `AGENT_KEY_BY_RUN_TYPE`, `HANDLERS`, the `/api` allow-list
  in `src/web/worker.mjs`, and this table. Tenant workflow routes (`/api/workflows…`)
  are allow-listed there as `tenantWorkflowRead`/`tenantWorkflowWrite`, mirroring the firm ones; run routes as
  `workflowRunRead`/`workflowRunWrite`, the recorder's computer-use protocol as
  `recorderComputerUseRead`/`recorderComputerUseWrite`. Payload changes must stay
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

- Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Postgres 16, MinIO/S3, uv; TypeSafe Jev
  (`VISTA_TYPESAFE_API_KEY`; stub answers without it — recorder workflow candidates and
  every Computer Use Agent judgment; `VISTA_COMPUTER_USE_*` thresholds/timeouts in
  `config.py`) beside the OpenAI-compatible model; Electron
  recorder (`src/recorder`, version 0.5.0; every change bumps `package.json` and pushes
  a `recorder-vX.Y.Z` tag, which builds and publishes the installers; `overrides` pins
  `tar` ≥ 7.5.21 because `get-windows` → `node-pre-gyp` pulled a vulnerable `tar`);
  Cloudflare Worker + static web; Node 22 for JS tests (installed under
  `~/.local/bin`).
- Start infra: `docker compose up -d` (MinIO image is `quay.io/minio/minio`).
- Migrate everything + bucket: `uv run python -m vista.manage migrate`
- API: `VISTA_COOKIE_SECURE=false uv run uvicorn vista.main:app --reload`
- Worker (agent jobs): `uv run python -m vista.jobs.worker`
- Tests: `uv run pytest` (DB tests skip if Postgres down; live-model tests are
  opt-in via `pytest -m live`) · `npm test` · lint: `uv run ruff check . && uv run
  ruff format --check .` (repo-wide; CI enforces) · `npx wrangler deploy --dry-run`
- Provisioning (operator-only): `python -m vista.manage migrate|create-workspace|
  add-user|rotate-key|link-workspace|seed-portfolio|load-synthetic`; in AWS via
  `deploy/aws/manage.sh` (prefer `--output-file PATH` so keys skip CloudWatch).
  `link-workspace --firm <firm slug> --company <slug> --deal <workspace deal id>` points a portfolio
  company at a workspace tenant provisioned earlier with `create-workspace`; the six
  live demo workspaces need this once, then `load-synthetic` reloads canonical rows
  into the linked tenants (it keeps an existing link).
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
- **Pull in recent changes first.** At the start of any work, before opening a
  PR, and before every push: `git fetch origin` and merge `origin/main` into the
  working branch (never rebase shared branches, never force-push). `main` moves
  between sessions (recorder releases, deploy docs); a PR based on a stale
  `main` is not ready. Re-run lint and the affected tests after the merge.
- Verify `origin` points to `emiliano800/vista` before pushing. Never fetch or
  push all remotes. Preserve unrelated user edits.
- Never commit secrets, `.env`, `deploy/aws/.env`, or credential-output files.

## Live deployment (2026-09-24)

- AWS account 630396228214, us-east-1, CloudFormation stack `vista`: ECS cluster
  `vista` with `vista-api` and `vista-worker` (WorkerDesiredCount=1), model
  **gpt-6-astra** (key in Secrets Manager `vista/openai-api-key`; TypeSafe key, when
  supplied, in `vista/typesafe-api-key` as `VISTA_TYPESAFE_API_KEY` — without it Jev is
  stub and the Computer Use Agent executes nothing. Live since 2026-09-24 17:05 UTC:
  Jev is reached through OpenRouter, not TypeSafe directly — stack parameter
  `TypeSafeBaseUrl` = `https://openrouter.ai/api/v1` (same `/systemone` API), model
  `~typesafe/jev-latest` (answers as `typesafe/jev-1.13-…`), and an OpenRouter key as
  the TypeSafe key; all three sit in `deploy/aws/.env`. The OpenRouter account must
  hold credits: a key on an account with none answers HTTP 402 on every judgment,
  which is worse than the stub, so smoke-test one judgment before deploying a key.
  ECS reads a secret only at task start — after changing the secret's VALUE alone,
  `aws ecs update-service --force-new-deployment` both services (deploy profile) or the
  running tasks keep the old value), RDS
  `vista-postgres`, S3 `vista-reports-630396228214`, endpoint
  `https://vi-6526b1efec4446e48c627173e9e805ce.ecs.us-east-1.on.aws`.
- Cloudflare Worker `vista` serves bumpsolutions.org and auto-builds on push
  (`npx wrangler deploy`); it proxies an explicit `/api` allow-list — new API routes
  must be added to `src/web/worker.mjs` or they 404 in production. Backend routes
  outside the allow-list today (`POST /api/runs`, `/api/employees`, deal documents,
  `PATCH /api/agents/{id}`, `POST /api/evals`, recordings `files/…/text` and
  `media/{name}`) are unreachable from the site; nothing in `src/web/public` calls
  them. The allow-list is GET-only for `/api/health`, so a HEAD probe 404s.
- Deploys: `deploy/aws/deploy.sh` needs a deployment-capable identity and Docker
  Desktop running (the binary is `~/.docker/bin/docker`, not on PATH by default). The
  local AWS CLI default is the scoped `emiliano-vista-operator` (Keychain): it can run
  manage tasks and read logs but CANNOT deploy (no UpdateStack/ECR push). The
  `vista-deploy` CLI profile assumes role `vista-deployer` from that key and CAN
  deploy: `AWS_PROFILE=vista-deploy PATH=$HOME/.docker/bin:$PATH deploy/aws/deploy.sh`.
  Deploy from a clean tree (untracked files count) or the image tag ends in `-dirty`.
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
- Deployed state 2026-09-25: image `526153c` (task defs `vista-vista-api:30`,
  `vista-worker:27`) went live at 02:47 UTC from a clean tree via the `vista-deploy`
  profile, after `70fac8d` (01:53) and `b7dd140` (02:31) the same night and five
  images on 2026-09-24. It carries PRs #10–#14, the full-detail upload contract
  (`activity-full-v1`, recorder 0.5.0) and the answer feedback loop (an employee's
  answer re-queues the analysis and is handed to Jev as `employee_answers`),
  platform migration `0005`, tenant `0020`–`0022`, the `/api/deals/{deal}/imports…` and
  `/api/companies/{id}/reports` routes, the six demo workspaces linked to their analyst
  companies, the firm-counter fix `18713e8`, the unreadable-amount import exception
  `e965454`, PRs #8–#9 (plan graph, browser/desktop harness drivers) and the recorder
  upload binding alias `3a449c4`. Two deploys failed the evening before (dev-only
  `httpx` import, then the `0022` rollback — see Gotchas). Deploy the image and the
  Worker together. An ECS deploy is a CANARY rollout (5 % for 3 min, then 3 min bake
  against `vista/vista-api/RollbackAlarm`) and takes about 10 minutes; AWS CLI
  timestamps print in local time (-04:00), not UTC.
- Recorder uploads and linked workspaces: linking a workspace tenant to its analyst
  company turns its only upload workspace into kind `company`; the company's `deal_id`
  stays an accepted alias so recorders enrolled before the link (binding
  `{kind: deal, id: <deal>}` in `~/Vista/cloud.json`) keep uploading.
- Recorder app names on macOS: launched normally on macOS 26, the app's `get-windows`
  helper answers with *no window* (no error, nothing to note), so every event carried an
  empty app through recorder 0.4.4; launched from a terminal the same helper works, so it
  is how macOS attributes the helper's window access. Since 0.4.5
  `src/recorder/src/foreground.js` falls back to LaunchServices (`lsappinfo`, no
  permission needed) for the frontmost app name — titles empty — and notes the reason
  once into the session manifest (`notes`). A report whose apps are all "Unknown app"
  has no transfers or loops, so the analysis never calls Jev — that is the first thing
  to check when "Jev is not being called". The second thing: Jev is only consulted
  when `workflow_candidates` finds a transfer (copy in A, paste in B within 120 s), a
  loop (A→B→A) or a stretch (≥4 switches); a short single-app recording has none, the
  report publishes with zero workflows, and no model call is made — by design. PRs
  #15–#21 (2026-09-25) were merged into a stacked chain of `devin/…` branches ending at
  `devin/1790299890-gates-ui`, not into `main`; merging that stack is a product decision.

## Gotchas

- Tenant Alembic env (`migrations/tenant/env.py`): no statements after
  `connection.commit()` before `context.configure` — silent rollback otherwise.
- `ruff format --check` is repo-wide in CI: format new files before pushing.
- The image installs `uv sync --no-dev`: every package `src/` imports must sit in
  `[project] dependencies`, never only in the `dev` group. A dev-only `httpx` import
  crashed every new API task on 2026-09-23 after the migrations had already run,
  which left the rolled-back image unable to start ("Can't locate revision 0022").
  CI now imports the app without the dev group to catch this.
- Tests drop every tenant they create (autouse fixture in `tests/conftest.py`, since
  2026-09-24) and share one global `platform.jobs` queue. Before that the local DB had
  accumulated ~2,900 test tenants at old revisions plus a job backlog, which broke the
  scheduler test (it iterates ALL tenants) and starved `_drain()` in `test_jobs.py`.
  If those symptoms return locally, run `python -m vista.manage migrate`, clear stale
  `queued` jobs, and drop leftover `firm-*` / `Firm *` tenants — not code changes.
- `next_ref` (firm display sequences `T-…`/`OP-…`) must re-read the firm row under
  its lock (`populate_existing`); a cached `Firm` in the session handed out a ref
  another transaction had committed (`OP-063`, 2026-09-24).
- OpenAI key + demo keys have passed through chats/public repo: rotate all of them
  before any real-customer use.
