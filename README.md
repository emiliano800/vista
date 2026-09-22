# Vista

Financial and operating intelligence for lower-middle-market private equity.
Vista is organized around **two platforms with three user views**: a financial
platform for the **PE analyst** and **portfolio-company CFO (portco CFO)**, and an
automation platform for the **forward-deployed engineer (FDE)**. They share canonical
company records, evidence, and agent results, with different scope and responsibilities.

The CFO view is the company-scoped subset of the analyst's financial view. The FDE
view focuses on workflows, automation delivery, and operational results. Financial
impact links back to that evidence; a proposed saving is not a realized result.

This is the product direction as of **2026-09-21**. The current code has analyst and
company workspaces; dedicated CFO/FDE experiences and their access controls are still
to be implemented. See the implementation map below.

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

## Who each platform serves

| Platform / view | Scope | Main questions and outputs |
| --- | --- | --- |
| **Financial — PE analyst** | Companies explicitly authorized for the PE firm | How are companies performing? What drives revenue, receivables, spend, and financial opportunities? Compare companies, inspect model inputs and assumptions, and distinguish potential benefits from measured results. |
| **Financial — portco CFO** | The CFO's assigned company within that same financial model | What is happening in my company? Review company financials, reconcile source data, investigate exceptions, and validate company-level impact. No sibling-company records or portfolio-wide comparisons. |
| **Automation — FDE** | Explicitly assigned companies and workflows | What should be automated, how will it work, and did it work? Inspect workflow evidence, configure and test proposed automations, track approvals, runs, failures, exceptions, and operational outcomes. |

The analyst and CFO use the same metric definitions, reporting periods, source
records, and calculations within their permitted scope. The CFO is a subset of
financial **visibility**, not necessarily a read-only user: editing and approval
rights must be specified separately. FDE access is a separate assignment, not an
automatic grant of all portfolio financial information. A person may hold multiple
roles only through explicit authorization.

Financial modeling is the focus of the financial platform; a complete forecasting,
valuation, or three-statement model is not implemented by the current metrics pages.
Keep unavailable inputs visible as missing rather than filling them with agent guesses.

## How the views connect

1. Import and validate company records with file/sheet/row provenance.
2. The financial views expose company results and, for the analyst, authorized
   portfolio comparisons and opportunities.
3. The FDE investigates the underlying workflow and proposes an automation with a
   baseline, expected outcome, and approval requirements.
4. Track implementation and measured operational results, such as cycle time,
   rework, error rates, and manual effort. Source-system changes require review.
5. The CFO and analyst see the relevant financial impact with its assumptions and
   evidence. Minutes saved alone do not establish realized cost savings.

This is the intended end-to-end workflow. Current findings, tasks, and run traces
provide a foundation; they do not constitute a shipped automation execution platform.
Employee recording and verification remain evidence inputs to these views, rather
than a fourth management platform.

## Current implementation map

| Piece | What it does |
| --- | --- |
| **PE analyst portfolio** (`/signin/analyst/` → `/portfolio/`) | Firm-scoped financial metrics, company drill-downs, opportunities and evidence; currently also mixes in imports, tasks, and agents that need separating by audience |
| **Company detail within analyst UI** (`/company/?id=…`) | Finance, records, findings, tasks, and agents for one company; still uses analyst authentication and a firm-wide snapshot, so it is not a CFO authorization boundary |
| **Existing company workspace** (`/signin/` → `/account/`) | Company data sources, findings, agents, run traces, and published recording reports; an operational foundation, not the finished CFO or FDE platform |
| **Agent suite** (`src/vista/agents/`) | File Reviewer, Sector Merger, Report Generator, Recording Reviewer — durable jobs with per-call cost tracking and eval harness |
| **Desktop recorder** (`src/recorder/`) | Electron recorder: on-device capture, consented metadata-only upload with selected documents, cloud analysis into a draft report the employee answers and publishes |
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
`create-workspace`, `add-user`, `rotate-key`, `seed-portfolio`, `load-synthetic`, and `migrate`
(public `POST /tenants` stays disabled unless a provisioning key is configured).
In AWS, run the same commands via `deploy/aws/manage.sh`.

The `.agents/skills/vista-api-e2e-testing` skill documents the full local E2E recipe,
including live-model runs and spend controls.
