# Vista — Next Steps

Ordered by value. Current state in `CURRENT_IMPLEMENTATION.md`; business sequencing
in `BUSINESS_COURSE_OF_ACTION.md`.

## Hackathon priority update — ingestion first

A local rebuild now implements a file-ingestion company workspace: upload exports,
confirm mappings, calculate source-linked findings, review and export evidence.
See `deploy/INGESTION_DEMO.md` for the working Meridian flow and deployment sequence.
The next demo step is deploying the backend/migration and then the web frontend.
Extend supported record schemas and analysis coverage after that; connect imported
records to agent discovery when the deterministic evidence foundation is ready.
Recording reports now live under `/account/recordings/`. The longer-term three-surface
vision below remains useful, but employee recording is not the hackathon's lead story.

## 1. Rebuild the frontend toward the actual vision

**The current web workspace (recording-report viewer) is not the envisioned
product.** It's a functional stopgap: sign in, pick a company, inspect report rows.
The vision is three surfaces where autonomy lives in the backend and the frontend's
job is trust:

### Employee surface — deliberately almost nothing
The employee never sees dashboards, agents, findings, or reports. Ideally they never
log into Vista at all:
- **Onboarding (once, ~5 min):** an admin registers them (name + role title); the
  employee answers 3–4 guided intake questions, phone-friendly, plus recording
  consent where applicable.
- **Ongoing:** agents work invisibly on schedule. When a finding needs verification,
  the employee gets ONE plain-language question in a channel they already use
  (email/Slack): *"Do you retype invoice amounts from email PDFs into QuickBooks?
  (Yes / No / Sort of)"* — one tap, mapped to a findings update behind the scenes.
  Mental model: "something occasionally asks me a smart question," never "an AI is
  monitoring me." This is the adoption make-or-break at acquired companies.
- The desktop recorder should follow the same principle: install-and-forget, clear
  consent up front, no employee-facing analytics.

### Company view (office manager / integration sponsor)
- Header: latest company summary (`GET /summaries/latest`) + stats chips.
- Findings queue (`GET /findings?status=open`): kind badge, title, evidence box
  (confidence + verify_by) front and center, three buttons — Reviewed / Dismissed /
  Actioned.
- Team panel (`GET /agents`): "Maria — Bookkeeper — last analyzed 2h ago," pause/
  resume, run now. Never "Agent #7."
- Audit drawer: any finding → its run's full event log (`GET /runs/{id}`) — what you
  show a skeptical CFO. Recording-report evidence appears here as supporting source
  data, not as its own product.

### PE firm view (operating partner)
- Portfolio grid: one tile per company — summary headline, open findings, cost.
  Click through to the company view.

Highest-value first screen: **company view + findings queue**, with the current
report viewer folded in as the evidence layer.

## 2. Join the two pipelines: recordings → findings

Today desktop reports and employee-agent findings are parallel systems. Make
uploaded recording reports an input to discovery runs: a `read_recordings` tool the
discovery handler calls, so findings cite actual observed activity (report rows)
instead of role hypotheses — confidence rises, `verify_by` becomes a link to
evidence. This is the single highest-leverage backend change: it turns the recorder
into the agent's first real sensor.

## 3. Portfolio level

`portfolio_summary` run type reading company summaries/findings across every tenant
a firm is authorized to see; requires a `firms → companies` relation in the platform
schema. Same map-reduce pattern as company summaries. This is also the hackathon
synergy agent (purchasing overlap, software overlap, cross-sell).

## 4. Connectors — the autonomy ladder continues

- **Email (read-only) + QuickBooks/Xero sandbox** first — most of a bookkeeper's job
  is visible in these two. Add `connections` (provider, encrypted OAuth tokens,
  scopes) and connector tools for discovery runs.
- Ladder after that: guided shadowing (interviews + approved-app observation) →
  scoped computer-use execution with review gates. Consent, retention, and redaction
  policy must be certified with a real (not synthetic) capture session first.

## 5. Company-workspace records (hackathon demo needs)

CRM-lite per tenant: customers, invoices, vendor purchases, subscriptions, tasks;
CSV import with agent-proposed field mappings + one ambiguous-record review step;
findings gain a "create task" action.

## 6. Operational hardening

- Rate limiting + auth monitoring on the public API (blocker for broad customer use).
- Workforce SSO; dedicated deployment role (operator is a scoped IAM user today).
- Scheduler as a deployed service; private subnets/NAT; WAF; CI deploys via OIDC.
- Secrets manager for the OpenAI key (rotate the one shared in chat).
- Backend recomputation/validation of report metrics rather than trusting uploads.

## 7. Later

Temporal (or similar) if agent workflows outgrow the Postgres queue; per-document
ACLs; RAG over tenant documents; usage-based billing from `usage_events`; model
routing (cheap discovery, stronger summaries).
