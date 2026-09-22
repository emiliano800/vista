# Vista — Current Implementation

What exists today, verified against the code and the live deployment.
(Roadmap: `NEXT_STEPS.md`. Business: `BUSINESS_COURSE_OF_ACTION.md`.)

## System map

```
src/vista/            FastAPI backend
  agents/             agent suite: discover/propose/execute/analyze phases,
                      LLM runtime + cassettes, eval harness, synthetic-data tools
  portfolio/          firm-scoped PE portfolio: access, imports, metrics,
                      processors, serializers, service, state
  api/                routers: sessions, deals, employees, findings, runs, usage,
                      analytics, evals, imports, portfolio, recordings, summaries,
                      synthetic, tenants
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
  (`create-workspace`, `add-user`, `rotate-key`, `provision-firm`,
  `seed-portfolio-demo`, `migrate`) — in AWS via `deploy/aws/manage.sh`.
- **Roles:** platform admin/member plus per-deal owner/member/viewer; firm
  memberships (analyst/operator/admin/viewer) gate the portfolio side. Run-starting
  requires owner on a deal (or admin).

## Canonical business data + imports

Tenant schemas hold canonical records with **row-level provenance**
(`data_source_type`, source file, import job, `synthetic_demo` flag): customers,
invoices, vendors, vendor purchases, subscriptions, tasks — plus the raw source
layer (`source_files`, import jobs/mappings/exceptions). Imports flow
upload → analyze → mapping review → validate → import, with original files kept in
S3 and every committed row traceable back. The import processor is pluggable
(`VISTA_IMPORT_PROCESSOR=demo|agent`; deterministic demo parser today).

## The agent suite

Four agents, all running as durable jobs through the same queue/worker, every model
call metered into `usage_events` (tokens + $ at real model rates):

| Agent | Trigger | What it does |
| --- | --- | --- |
| **File Reviewer** | on demand / scheduled | Profiles a division's tables in code, has the model interpret them, writes observed-fact findings with file/column evidence + confidence |
| **Sector Merger** | on demand | Portfolio Analyst across sister companies in a sector; one call per opportunity kind; proposals cite companies, shared keys, table refs; look-alike traps get rejected and logged |
| **Report Generator** | on demand | Company summary over open findings; keeps verified facts separate from hypotheses |
| **Recording Reviewer** | recorder submit | Explains low-confidence stretches of recordings; employee approves/fixes/explains; below-threshold always waits for the employee |

Infrastructure around them: append-only `agent_runs`/`agent_run_events` traces
(every tool call and model call), findings with kind/status triage, `/usage` with
group-by (company/division/sector/model/agent_key/run_type), `/agents/analytics`,
eval runs scored against `synthetic_data/answer_key.json` (precision/recall/trap
hits, `scripts/eval_agents.py`), and `scripts/llm_smoke.py` as the tier-0 provider
check. Reasoning models (gpt-5+/gpt-6/o-series) are handled correctly
(`max_completion_tokens`, no forced temperature, raised budgets).

## Desktop recorder

Electron app: one Start button, always-on-top orb overlay, on-device capture
(screenshots/video/keystrokes never leave the machine), document tracking
(AXDocument/lsof/Spotlight) with last-version snapshots, file agent at Stop,
sensitive-section flags, keyboard/mouse insights with approval, suggested
workflows/trends, hidden demo mode, and an agent-facing HTTP API with a batch
review pipeline. Only completed, employee-reviewed report bundles upload
(idempotent, content-addressed).

## Web surfaces

Static pages on the Field Notes system: landing `/`, sign-in `/signin/`, analyst
sign-in `/signin/analyst/`, company workspace `/account/` (Overview / Data sources /
Findings / Agents / Runs, run-trace dialog, recording evidence under
`/account/recordings/`), and the analyst portfolio UI. A Cloudflare Worker serves
assets and proxies an explicit `/api` allow-list to the backend.

## Live deployment (AWS, account 630396228214, us-east-1)

One CloudFormation stack `vista`: ECS Fargate `vista-api` + `vista-worker` (worker
enabled, model **gpt-6-astra** via Secrets Manager), RDS Postgres (schema history in
Alembic: platform ×3, tenant ×14 migrations, migrate-on-start with advisory lock),
private versioned S3 `vista-reports-630396228214`, CloudWatch logs. Cloudflare
serves bumpsolutions.org and proxies `/api`. Deploys: `deploy/aws/deploy.sh` from
**emiliano800/vista main only** (needs a deployment-capable identity; the scoped
operator user can manage but not deploy). Demo tenants are seeded with reports
generated from the synthetic companies' own data; agent runs execute in AWS and are
visible in the workspaces.

## Tests

~140 Python tests (isolation, permissions, jobs, agents + evals, imports,
recordings/review, web/session, config, AWS script safety) + ~87 JS tests
(recorder units, workspace UI, worker proxy). Live-model tests are opt-in
(`pytest -m live`); everything else runs on stubs and spends nothing. CI runs
lint (`ruff check` + `ruff format --check`), both suites against a Postgres
service container, and a wrangler dry-run on pushes to this repository's main branch.

## Known limits

- Recording reports are not yet inputs to File Reviewer findings (parallel
  pipelines; join is roadmap).
- Findings on real (non-synthetic) company data depend on the import pipeline;
  the agent import processor is stubbed behind a flag.
- Public auth still lacks rate limiting; workforce SSO pending. Demo keys are
  deliberately public (synthetic data only); the OpenAI key and demo keys should
  be rotated before any real-customer use.
- Migration gotcha: in `migrations/tenant/env.py`, never execute statements after
  `connection.commit()` before `context.configure` (Alembic silently rolls back).
