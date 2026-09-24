# Vista — Current Implementation

Implementation reference; checked against repository commit `6b5ad5f` on
2026-09-24. Deployment notes are historical and must be rechecked against AWS before
assuming a newly pulled change is live. The ledger/company-import commits of
2026-09-23 (platform migration `0005`, tenant migrations `0020`–`0022`) went live on
2026-09-24 as image `b28094d`; image `53968b1`, deployed the same day at 13:24 UTC
(followed by `e965454` at 18:19, `d16fb61` at 22:02 with PRs #8–#9 and the recorder
upload binding alias, and `9ca5ecb` at 22:40 with PRs #10–#12),
adds the firm-counter race fix (`18713e8`) and the test tenant teardown (`6b5ad5f`).
Production Jev went live the same day at 17:05 UTC through OpenRouter (stack
parameter `TypeSafeBaseUrl`, commit `c77d871`), so recorder workflow candidates and
Computer Use judgments are no longer stubbed there.
(Roadmap: `NEXT_STEPS.md`. Business: `BUSINESS_COURSE_OF_ACTION.md`.)

## Product direction and current coverage

The target is **two platforms with three views**: a financial platform for the PE
analyst and portco CFO, and a workflow-automation platform for the FDE
(forward-deployed engineer). The CFO sees only the assigned company's subset of the
analyst's financial model. FDEs see assigned workflow automations and operational
results, linked to financial impact with evidence and explicit assumptions.

| Area | Implemented foundation | Remaining separation |
| --- | --- | --- |
| PE analyst | Firm-scoped portfolio, company finance drill-downs, canonical records, opportunities and evidence | Center navigation and results on financial analysis; broader forecasting/modeling is not implemented |
| Portco CFO | Company isolation and reusable financial metrics | CFO role, company-scoped financial API responses and UI; `/company/` currently requires analyst access and receives the firm-wide snapshot |
| FDE | Company evidence workspace: canonical imports (same contract as the analyst), findings, tasks, agents, run traces, and recorder/task-mining output | Assigned-workflow access, dedicated automation configuration/testing/approval and results experience; source-system automation execution is not a shipped platform |

Current firm memberships use `analyst/operator/admin/viewer`, not dedicated `cfo`
or `fde` roles. Financial visibility and mutation rights need separate decisions.
FDE access must not implicitly grant portfolio financial visibility. Use the same
financial calculations for analyst and CFO, and distinguish operational improvement,
modeled financial benefit, and validated realized impact.

## System map

```
src/vista/            FastAPI backend
  agents/             agent suite: discover/propose/execute/analyze phases,
                      LLM runtime + cassettes, eval harness, synthetic-data tools
  portfolio/          firm-scoped PE portfolio: access, imports, interpret,
                      ledger, metrics, processors, serializers, service, state
  api/                routers: sessions, deals, employees, findings, runs, usage,
                      analytics, evals, company_imports, portfolio, recorder,
                      recordings, summaries, synthetic, tenants, workflows
  jobs/               Postgres-backed durable queue, worker, scheduler, handlers
src/taskmining/       on-device task-mining pipeline (redact → sessionize →
                      discover → analytics → report bundle)
src/recorder/         Electron desktop recorder + overlay + agent-facing HTTP API
src/web/              website + workspaces (static, Field Notes design) +
                      Cloudflare Worker proxy
deploy/               Docker hosting + AWS CloudFormation (ECS/RDS/S3) + ops scripts
synthetic_data/       6 synthetic companies (2 sectors, 3 data-quality tiers),
                      manifest + planted answer key
```

## Tenancy and access

- **Schema-per-tenant Postgres isolation.** Each company is its own schema; the
  shared `platform` schema holds tenants, users, browser sessions, the job queue —
  and the firm layer: `firms`, `firm_memberships`, `firm_companies` (which firm may
  see which company tenant), firm-scoped `opportunities` and portfolio activity.
- **Auth:** access keys stored as SHA-256 digests only; browser sessions are
  expiring HttpOnly cookies bound to the key hash; key rotation kills sessions.
  Custom header + origin allow-list on cookie writes (CSRF). Public tenant
  provisioning is disabled; operators use `python -m vista.manage`
  (`create-workspace`, `add-user`, `rotate-key`, `link-workspace`, `seed-portfolio`,
  `load-synthetic`, `migrate`) — in AWS via `deploy/aws/manage.sh`.
- **Roles:** platform admin/member plus per-deal owner/member/viewer; firm
  memberships (analyst/operator/admin/viewer) gate the portfolio side. Run-starting
  requires owner on a deal (or admin).

## One ledger under every view (2026-09-23)

The analyst workspace, the company workspace and the recorder read and write the
same tenant tables; nothing is mirrored per surface.

- `platform.firm_companies.deal_id` names the Deal inside each company tenant that
  the company workspace and the recorder scope by. Creating a company creates its
  Deal; the platform migration backfilled existing companies by name. A workspace
  provisioned with `create-workspace` before its company existed lives in a
  different tenant than the analyst's company: `manage link-workspace --company
  <slug> --deal <workspace deal>` re-points the company at that tenant (the
  workspace otherwise answers 409 "not linked to a portfolio company"), and
  `load-synthetic` then reloads canonical rows into the linked tenant.
- `findings` / `agent_runs` / `agent_run_events` / `usage_events` /
  `company_summaries` are the only agent ledger. `findings` carries the analyst's
  display ref (`F-012`), the firm company id and a demo flag; the former
  `workspace_findings` / `workspace_agents` / `workspace_agent_runs` mirror is gone.
  `vista.portfolio.ledger` derives the analyst's agents, runs and findings from the
  ledger (an "agent" is one tenant's runs grouped by `agent_key`; a run's narrative
  is its event stream; its cost is the sum of its metered calls). Sector Merger
  runs live in the firm's home tenant and appear firm-level, without a company.
- Finding triage is one write: `POST /api/findings/{ref|uuid}/status` (analyst) and
  `PATCH /api/findings/{id}` (company workspace) change the same row; dismissed
  findings are invisible to downstream agents either way.
- Company workspace imports run the canonical import contract by Deal
  (`/api/deals/{deal}/imports…`, `/records`, `/import-datasets`) and
  `POST /api/deals/{deal}/review` queues the same `canonical_review` job the
  analyst's portfolio analysis runs per company. The former JSON `import_batches`
  path and its rule engine (`vista.ingestion`) are removed.
- Published recorder reports reach the analyst's company page read-only
  (`GET /api/companies/{id}/reports`), and `/api/agents/analytics` aggregates
  across a firm member's company tenants.

## Canonical business data + imports

Tenant schemas hold canonical records with **row-level provenance**
(`data_source_type`, source file, import job, `synthetic_demo` flag): customers,
invoices, vendors, vendor purchases, subscriptions, policies, purchase orders and
lines, inventory balances, tasks — plus the raw source layer (`source_files`,
import jobs/mappings/exceptions). Imports flow upload → detect → mapping review →
validate (exceptions) → approve → canonical rows, with original files kept in S3
and every committed row traceable back. The same contract serves the analyst
wizard (`/api/companies/{id}/imports…`) and the company workspace
(`/api/deals/{deal}/imports…`). The import processor is pluggable
(`VISTA_IMPORT_PROCESSOR=demo|agent`; deterministic demo parser today).

## The agent suite

Five agents, all running as durable jobs through the same queue/worker, every model
call metered into `usage_events` (tokens + $ at real model rates):

| Agent | Trigger | What it does |
| --- | --- | --- |
| **File Reviewer** | on demand / scheduled | Profiles a division's tables (or, as `canonical_review`, a company's canonical rows) in code, has the model interpret them, writes evidence-linked findings with a firm-wide ref; started from the analyst's portfolio analysis or from the company workspace |
| **Sector Merger** | on demand | Portfolio Analyst across sister companies in a sector; one call per opportunity kind; proposals cite companies, shared keys, table refs; look-alike traps get rejected and logged |
| **Report Generator** | on demand | Company summary over open findings; keeps verified facts separate from hypotheses |
| **Recording Reviewer** | recorder upload | v2: analyses an accepted metadata-only package into a private draft report (facts computed in code, model interpretation kept apart, questions for the employee) that only the employee can publish; v1 (legacy installs): explains low-confidence stretches for approve/fix/explain |
| **Computer Use Agent** | Workflows → Run in sandbox (tenant admin) | Executes an *approved* sandbox workflow version one bounded step at a time — Jev picks the next action/target/input among code-enumerated candidates, code enforces limits and the risk gate, browser/desktop steps go through the employee's recorder with consent, an independent read-back judgment verifies the result |

Infrastructure around them: append-only `agent_runs`/`agent_run_events` traces
(every tool call and model call), findings with kind/status triage, `/usage` with
group-by (company/division/sector/model/agent_key/run_type), `/agents/analytics`,
eval runs scored against `synthetic_data/answer_key.json` (precision/recall/trap
hits, `scripts/eval_agents.py`), and `scripts/llm_smoke.py` as the tier-0 provider
check. Reasoning models (gpt-5+/gpt-6/o-series) are handled correctly
(`max_completion_tokens`, no forced temperature, raised budgets).

## Desktop recorder

Electron app: one Start button, always-on-top orb overlay, on-device capture, document tracking
(AXDocument/lsof/Spotlight) with last-version snapshots, file agent at Stop,
sensitive-section flags, keyboard/mouse insights with approval, suggested
workflows/trends, hidden demo mode, and an agent-facing HTTP API with a batch
review pipeline. A connected app uses the **Upload session** flow: after Stop the
employee approves a sharing package (activity metadata only — timestamps, app names,
interaction types, counts — plus explicitly selected document snapshots), a disk-backed
queue uploads it with checksum-bound signed URLs, the server verifies and accepts it,
and the worker's Recording Reviewer analyses it into a private draft report with
focused questions. The employee answers and publishes with a second explicit consent;
only then does the report appear in the company workspace's Recordings view. Local
originals are retained. The older v1 path (local Python analysis, report + media
upload) still exists server-side for old installs but is not used by a connected app.
See `src/recorder/README.md` for the capture and upload contract.

## Web surfaces

Static pages on the Field Notes system: landing `/`, sign-in `/signin/`, analyst
sign-in `/signin/analyst/`, company workspace `/account/` (Overview / Data sources
(canonical records with provenance + import history) / Findings (import exceptions +
agent findings) / Agents / Runs / Recordings, a three-step import dialog over the
canonical contract, run-trace dialog, and a report dialog for published recording
reports), and the analyst portfolio UI, whose company page also lists published
recordings. A Cloudflare Worker serves assets and proxies an explicit `/api`
allow-list to the backend.

## Live deployment (AWS, account 630396228214, us-east-1)

One CloudFormation stack `vista`: ECS Fargate `vista-api` + `vista-worker` (worker
enabled, model **gpt-6-astra** via Secrets Manager), RDS Postgres (schema history in
Alembic: platform ×5, tenant ×21 migrations in `main`; migrate-on-start with advisory lock),
private versioned S3 `vista-reports-630396228214`, CloudWatch logs. Cloudflare
serves bumpsolutions.org and proxies `/api`. Deploys: `deploy/aws/deploy.sh` from
**emiliano800/vista main only** (needs a deployment-capable identity; the scoped
operator user can manage but not deploy). Demo tenants are seeded with reports
generated from the synthetic companies' own data; agent runs execute in AWS and are
visible in the workspaces.

## Tests

181 Python tests (isolation, permissions, jobs, agents + evals, canonical imports
from both surfaces, the interpretation chain, recordings/review, web/session, config,
AWS script safety) + 126 JS tests (recorder units, workspace UI, worker proxy). Live-model tests are opt-in
(`pytest -m live`); everything else runs on stubs and spends nothing. CI runs
lint (`ruff check` + `ruff format --check`), both suites against a Postgres
service container, and a wrangler dry-run on pushes to this repository's main branch.

## Known limits

- Recording reports (v1 and the new published v2 reports) are not yet inputs to
  File Reviewer findings (parallel pipelines; join is roadmap). Published reports
  cannot be withdrawn yet, and the recorder's local upload queue is never purged.
- The v2 analysis is metadata-only by design: activities are known at application
  level; the model's interpretation is a labelled hypothesis, not an observed fact.
- Findings on real (non-synthetic) company data depend on the import pipeline;
  the agent import processor is stubbed behind a flag.
- The retired JSON import path carried an insurance commission-statement
  reconciliation (statement premium × policy rate vs commission paid). Commission
  statements and carrier agreements are not canonical datasets yet, so that check
  has no home until they are added to the import contract and `canonical_checks`.
- Public auth still lacks rate limiting; workforce SSO pending. Demo keys are
  deliberately public (synthetic data only); the OpenAI key and demo keys should
  be rotated before any real-customer use.
- Migration gotcha: in `migrations/tenant/env.py`, never execute statements after
  `connection.commit()` before `context.configure` (Alembic silently rolls back).
- Computer Use Agent: sandbox only; the recorder in this build ships placeholder
  browser/desktop harnesses (steps come back `harness_unsupported` and the run pauses
  for a person); firm-side run routes and a run-inputs UI are not built; without
  `VISTA_TYPESAFE_API_KEY` the agent executes nothing.
