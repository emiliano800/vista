# Vista — Further Steps (Technical Roadmap)

Where the product goes from the current backend (`IMPLEMENTATION_SO_FAR.md`).
Ordered roughly by value-per-effort.

## 1. Frontend (next build)

Three surfaces for three audiences. Core principle: **autonomy lives in the backend;
the frontend's job is trust.**

### Employee surface — deliberately almost nothing
The employee never sees dashboards, agents, or findings. Ideally they never log into
Vista at all:
- **Onboarding (once, ~5 min):** admin registers them (`POST /employees` — name +
  role title); the employee answers 3–4 guided intake questions, phone-friendly.
- **Ongoing:** the agent works invisibly on its schedule. When a finding needs
  verification, the employee gets ONE human question in a channel they already use
  (email/Slack): *"Do you retype invoice amounts from email PDFs into QuickBooks?
  (Yes / No / Sort of)"* — one tap, mapped to `PATCH /findings/{id}` + evidence
  update behind the scenes. Their mental model: "something occasionally asks me a
  smart question" — never "an AI is monitoring me." This is the adoption make-or-break
  at acquired companies.

### Company view (office manager / integration sponsor)
- Header: latest company summary (`GET /summaries/latest`) + stats chips.
- Findings queue (`GET /findings?status=open`): kind badge, title, detail, prominent
  evidence box (confidence + verify_by), three buttons — Reviewed / Dismissed /
  Actioned.
- Team panel (`GET /agents`): "Maria — Bookkeeper — last analyzed 2h ago", pause/
  resume, Run discovery now. Never "Agent #7".
- Audit drawer: any finding → its run's event log (`GET /runs/{id}`). What you show a
  skeptical CFO.

### PE firm view (operating partner)
- Portfolio grid: tile per company — summary headline, open-finding count, cost
  (`GET /usage`). Click through to company view.

Highest-value first screen: **company view + findings queue**.

## 2. Portfolio-level summary

Same map-reduce pattern one level up: a `portfolio_summary` run type that reads the
company summaries/findings of every tenant a firm is authorized to see and produces
the cross-company brief. Requires a `firms → companies` relation in the platform
schema (today each tenant is standalone). This is also the hackathon synergy agent
(purchasing overlap, software overlap, cross-sell).

## 3. Connectors — Phase 2 of the autonomy ladder

Graduate findings from role-hypotheses to observed facts:
- **Email (IMAP/Gmail, read-only)** and **QuickBooks/Xero sandbox** first — these two
  expose most of a bookkeeper/office-manager's job.
- Add `connections` (per employee-agent: provider, OAuth tokens encrypted, scopes)
  and connector tools the discovery handler can call.
- Evidence model already supports this: findings cite message/transaction IDs instead
  of "role-hypothesis", confidence rises, `verify_by` becomes a link.

Ladder after that: **Phase 3** guided shadowing (interviews + observing approved
apps), **Phase 4** scoped computer-use execution with review gates. Consent,
recording policy, and retention rules must ship BEFORE any observation feature.

## 4. Company-workspace records (hackathon needs)

CRM-lite tables per tenant: customers, invoices, vendor purchases, subscriptions,
tasks — plus CSV import with agent-proposed field mappings and one ambiguous-record
review step. Findings gain a "create task" action (`findings → tasks`).

## 5. Hardening (before any real deployment)

- Hash API tokens; gate `POST /tenants` behind an ops credential; real IdP/SSO for users.
- Secrets manager for the OpenAI key (rotate the current one — it was shared in chat).
- Rate limits, request logging, structured app logs, Sentry-style error tracking.
- CI: run pytest against a Postgres service container on every push.
- AWS deploy path: done in `deploy/aws/` (ECS Express Mode API + RDS + S3 + optional
  worker). Remaining: scheduler service, private subnets/NAT, WAF, CI deploys via OIDC.

## 6. Later / keep an eye on

- Temporal (or similar) if agent workflows outgrow the Postgres queue (long
  multi-step runs with human-in-the-loop pauses).
- Per-document ACLs, RAG over tenant documents, usage-based billing from
  `usage_events`, model routing (cheap model for discovery, stronger for summaries).
