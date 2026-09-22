# Vista — Next Steps

Ordered by value. Current state: `CURRENT_IMPLEMENTATION.md`. Business sequencing:
`BUSINESS_COURSE_OF_ACTION.md`.

## 1. Separate the financial and automation platforms

Product direction (2026-09-21): two platforms, three role-specific views.

- **PE analyst:** financial model and performance across the firm's authorized
  companies, company comparisons, assumptions, opportunities, and validated impact.
- **Portco CFO:** the assigned company's subset of that same financial view, using
  identical metric definitions, reporting periods, and evidence. Define company
  editing/approval rights separately from financial visibility.
- **FDE (forward-deployed engineer):** assigned workflows, automation proposals and
  configuration, tests, approvals, execution status, exceptions, and measured
  operational results. Financial impact is linked back to this evidence.

Start from the current analyst portfolio/company finance UI and company evidence
workspace. Split navigation and responsibilities without creating competing financial
models. Preserve the shared canonical data, durable jobs, and provenance underneath;
separate product views do not require separate backend stacks.

Implement authorization before exposing the new views. The current `/company/` page
loads the analyst shell and full firm snapshot; hiding the company list cannot make
it a CFO portal. Add explicit CFO company scope and FDE company/workflow assignments,
apply them to reads, writes, downloads, exports, and runs, and test denied cross-company
access. Do not equate the current firm `operator` role with the planned FDE role.

Financial work should build on existing revenue, AR, vendor-spend and sector metrics.
Define missing financial-model inputs and calculations before promising forecasting
or valuation. FDE work should distinguish proposed, approved, implemented, and measured
outcomes; existing findings and run traces are not an automation executor.

## 2. Validate the shared data-to-result loop

Canonical imports and the company-review → sector-merge interpretation chain are in
the code. Verify the deployed image before running the new `load-synthetic` management
command: pulling Git alone does not update AWS tasks. Seed only the intended demo
scope and update `DEMO_ACCESS.md` if identities change.

Validate one company from source CSV/XLSX through mapping review, exceptions,
canonical rows, financial metrics, and reviewer findings. Verify identical financial
results for CFO and analyst at the same company/period scope. Then connect an FDE
workflow investigation to an approved automation, operational baseline and measured
outcome, and financial validation. That final automation-to-impact loop is still
implementation work; never treat modeled savings as realized results.

## 3. Join recordings into the evidence graph

Uploaded recording reports still live beside, not inside, agent discovery. Add a
`read_recordings` tool so File Reviewer findings can cite observed desktop activity
(report rows) alongside file evidence — recordings become the agents' first real
sensor on non-synthetic work.

## 4. Connectors (the autonomy ladder continues)

The Computer Use Agent already executes approved workflows in the sandbox (bounded
primitives, review gates, verification). What it lacks is *reach*: the recorder's page and
desktop drivers, OAuth connectors, and a connections API/UI.

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
