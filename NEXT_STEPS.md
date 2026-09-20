# Vista — Next Steps

Ordered by value. Current state: `CURRENT_IMPLEMENTATION.md`. Business sequencing:
`BUSINESS_COURSE_OF_ACTION.md`.

## 1. Deploy and seed the firm-scoped portfolio

The firm-scoped architecture (platform `firms`/`firm_companies`/`opportunities`,
canonical business records with provenance, import pipeline, portfolio APIs) is
merged but **not yet deployed** — production runs the previous backend. Next deploy
(`deploy/aws/deploy.sh`) applies platform migration 0003 + the new tenant
migrations, then `deploy/aws/manage.sh seed-portfolio-demo` seeds the demo firm.
Update `DEMO_ACCESS.md` afterwards if seeded identities/keys change.

## 2. Close the demo loop on real imports

The demo import processor is deterministic; the agent-backed processor sits behind
`VISTA_ENABLE_AGENT_IMPORT`. Ship one company's real (synthetic-file) CSV/XLSX
import end-to-end through mapping review → exceptions → committed canonical rows →
File Reviewer findings over *imported* records instead of raw division files.

## 3. Join recordings into the evidence graph

Uploaded recording reports still live beside, not inside, agent discovery. Add a
`read_recordings` tool so File Reviewer findings can cite observed desktop activity
(report rows) alongside file evidence — recordings become the agents' first real
sensor on non-synthetic work.

## 4. Connectors (the autonomy ladder continues)

Read-only email + QuickBooks/Xero sandbox first; `connections` table with encrypted
OAuth tokens; connector tools for discovery runs. Findings graduate from
file-profile facts to observed business activity; `verify_by` becomes a link.
Consent, retention, and redaction policy must be certified with a real capture
before any observation feature widens.

## 5. Hardening before real customers

- Rate limiting + auth monitoring on the public API (top blocker).
- Rotate the OpenAI key and all demo/analyst keys (they've passed through chats
  and a public repo); move teammate access to per-person IAM users; workforce SSO.
- CI deploys via OIDC from emiliano800 main (the only sanctioned deploy source);
  scheduler as a deployed service; private subnets/NAT; WAF.
- Backend recomputation of uploaded report metrics rather than trusting bundles.

## 6. Later

Temporal (or similar) if agent workflows outgrow the Postgres queue; per-document
ACLs; RAG over tenant documents; usage-based billing from `usage_events`; model
routing (cheap model for bulk discovery, flagship for summaries — gpt-6-astra
everywhere is demo-grade, not unit-economics-grade).
