# Vista

**Vista is the AI operating platform for private equity rollups.** It helps PE
firms integrate acquired businesses by connecting their systems, learning their
workflows, and deploying AI agents to run them.

Vista is delivered as a service with a software platform underneath. A PE firm
acquires a business and hands it to Vista; Vista is accountable for getting the
integration and automation live, rather than shipping software for the customer to
configure. Every deployment uses the same platform — recorder, workflow learning,
connectors, agent runtime, evaluation, permissions, and the portfolio data layer —
and every deployment is expected to make the next one need less Vista human labor.

The services flow for each acquired company:

1. **Connect** the existing systems (accounting, CRM, field-service, email, files).
2. **Map and record** employee workflows with the Vista recorder and imports.
3. **Build and deploy** agents that automate those workflows behind review gates.
4. **Monitor** exceptions and outcomes; improve the agents over time.
5. **Report** the unified operating and financial picture to the PE firm and CFO.

The analyst and CFO financial views are the reporting and visibility layer that
the service delivers, not a separate product. The FDE (forward-deployed engineer)
is the Vista person who runs a deployment. All three views share canonical company
records, evidence, and agent results. A proposed saving is not a realized result.

This is the product direction as of **2026-09-23**. The current code has analyst and
company workspaces, discovery agents, and the recorder; connectors, an agent runtime
that acts in customer systems, and dedicated CFO/FDE access controls are still to be
implemented. See the implementation map below.

**Live demo:** https://bumpsolutions.org — company sign-in at `/signin/`, PE analyst
sign-in at `/signin/analyst/`. Demo keys (synthetic data only): [DEMO_ACCESS.md](DEMO_ACCESS.md).

Docs:
- [APPLICATION_SCAFFOLDING_AND_NEXT_STEPS.md](APPLICATION_SCAFFOLDING_AND_NEXT_STEPS.md) — team implementation guide: workflow automation, verification, and learning
- [CURRENT_IMPLEMENTATION.md](CURRENT_IMPLEMENTATION.md) — what exists and how it works
- [NEXT_STEPS.md](NEXT_STEPS.md) — roadmap and open work
- [PRIVATE_EQUITY.md](PRIVATE_EQUITY.md) — short primer on private equity and how Vista maps onto it
- [BUSINESS_COURSE_OF_ACTION.md](BUSINESS_COURSE_OF_ACTION.md) — business plan + go-to-market
- [INFRASTRUCTURE.md](INFRASTRUCTURE.md) — infra primer: request path, AWS stack, deploys, runbook
- [DESIGN.md](DESIGN.md) — the "Field Notes" design system every surface uses
- [deploy/README.md](deploy/README.md) · [deploy/aws/README.md](deploy/aws/README.md) — hosting and AWS operations

## Who each view serves

| View | Scope | Main questions and outputs |
| --- | --- | --- |
| **Reporting — PE analyst** | Companies explicitly authorized for the PE firm | How are companies performing? What drives revenue, receivables, spend, and financial opportunities? Compare companies, inspect model inputs and assumptions, and distinguish potential benefits from measured results. |
| **Reporting — portco CFO** | The CFO's assigned company within that same financial model | What is happening in my company? Review company financials, reconcile source data, investigate exceptions, and validate company-level impact. No sibling-company records or portfolio-wide comparisons. |
| **Deployment — FDE** | Explicitly assigned companies and workflows | What should be automated, how will it work, and did it work? Inspect workflow evidence, configure and test proposed automations, track approvals, runs, failures, exceptions, and operational outcomes. This is Vista's own delivery surface. |

The analyst and CFO use the same metric definitions, reporting periods, source
records, and calculations within their permitted scope. The CFO is a subset of
financial **visibility**, not necessarily a read-only user: editing and approval
rights must be specified separately. FDE access is a separate assignment, not an
automatic grant of all portfolio financial information. A person may hold multiple
roles only through explicit authorization.

The reporting views show what the service has connected, automated, and measured;
a complete forecasting, valuation, or three-statement model is not implemented by
the current metrics pages. Keep unavailable inputs visible as missing rather than
filling them with agent guesses.

## How a deployment flows through the platform

1. Connect systems and import company records with file/sheet/row provenance.
2. Employees demonstrate workflows with the recorder; agents convert demonstrations
   and records into observed facts and proposed automations.
3. The FDE verifies the proposal — baseline, expected outcome, approval requirements —
   and deploys the agent within its permitted scope.
4. Agents run the workflow; exceptions route to people; every action is auditable.
   Track measured operational results such as cycle time, rework, error rates, and
   manual effort. Source-system changes require review.
5. The CFO and analyst see the relevant financial impact with its assumptions and
   evidence. Minutes saved alone do not establish realized cost savings.

This is the intended end-to-end workflow. Current findings, tasks, and run traces
provide a foundation; the Computer Use Agent adds one bounded, sandbox-only execution
slice with review gates — not a shipped automation platform for real systems.
The recorder is how Vista learns a company's operations without sending consultants
to interview every employee: the employee does the job, Vista observes and proposes.

The design test for every feature: **does it reduce the Vista human labor needed to
deploy the next company?** Reusable workflow library, recorder-to-agent learning,
standard connectors, and a portfolio-wide ontology pass; one-off custom code does not.

## Current implementation map

| Piece | What it does |
| --- | --- |
| **PE analyst portfolio** (`/signin/analyst/` → `/portfolio/`) | Firm-scoped financial metrics, company drill-downs, opportunities and evidence; currently also mixes in imports, tasks, and agents that need separating by audience |
| **Company detail within analyst UI** (`/company/?id=…`) | Finance, records, findings, tasks, and agents for one company; still uses analyst authentication and a firm-wide snapshot, so it is not a CFO authorization boundary |
| **Existing company workspace** (`/signin/` → `/account/`) | Canonical imports (same contract as the analyst), records with provenance, findings, agents, run traces, and published recording reports, all on the ledger the analyst reads; an operational foundation, not the finished CFO or FDE platform |
| **Agent suite** (`src/vista/agents/`, `src/vista/computer_use/`) | File Reviewer, Sector Merger, Report Generator, Recording Reviewer, Computer Use Agent — durable jobs with per-call cost tracking and eval harness; the Computer Use Agent runs approved sandbox workflows one bounded step at a time (see AGENTS.md) |
| **Desktop recorder** (`src/recorder/`) | Electron recorder: on-device capture, consented metadata-only upload with selected documents, cloud analysis into a draft report the employee answers and publishes; published reports show in the company workspace and on the analyst's company page |
| **Backend** (`src/vista/`) | FastAPI + Postgres (schema-per-tenant isolation) + S3; durable job queue; append-only run/audit trail |

Current financial metrics include invoiced revenue, outstanding and overdue AR,
vendor spend, and software costs, with sector-specific policy, purchasing, and
inventory records. Calculations live in `src/vista/portfolio/metrics.py`; firm scope
is enforced in `src/vista/portfolio/access.py`. Current firm roles are
`analyst/operator/admin/viewer`; there are no dedicated `cfo` or `fde` roles yet.
Implement company-scoped responses and FDE assignments on the backend before
exposing new views. Hiding portfolio navigation is not access control.

## Quickstart (local)

```sh
docker compose up -d                                # Postgres + MinIO
uv sync && cp .env.example .env
uv run python -m vista.manage migrate               # schemas + bucket
VISTA_COOKIE_SECURE=false uv run uvicorn vista.main:app --reload
uv run python -m vista.jobs.worker                  # separate terminal (agent jobs)
uv run pytest && npm test
```

Provisioning is operator-only: `uv run python -m vista.manage --help` lists
`create-workspace`, `add-user`, `rotate-key`, `link-workspace`, `seed-portfolio`, `load-synthetic`, and `migrate`
(public `POST /tenants` stays disabled unless a provisioning key is configured).
In AWS, run the same commands via `deploy/aws/manage.sh`.

The `.agents/skills/vista-api-e2e-testing` skill documents the full local E2E recipe,
including live-model runs and spend controls.
