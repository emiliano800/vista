# Vista

Operating intelligence for lower-middle-market private equity. A PE firm signs in
to a portfolio workspace over its acquired companies; each company has its own
isolated workspace; AI agents read the companies' data and produce evidence-linked
findings, cross-company opportunities, and reports — with every number traceable to
a source row and every model call metered.

**Live demo:** https://bumpsolutions.org — company sign-in at `/signin/`, PE analyst
sign-in at `/signin/analyst/`. Demo keys (synthetic data only): [DEMO_ACCESS.md](DEMO_ACCESS.md).

Docs:
- [CURRENT_IMPLEMENTATION.md](CURRENT_IMPLEMENTATION.md) — what exists and how it works
- [NEXT_STEPS.md](NEXT_STEPS.md) — roadmap and open work
- [BUSINESS_COURSE_OF_ACTION.md](BUSINESS_COURSE_OF_ACTION.md) — business plan + go-to-market
- [DESIGN.md](DESIGN.md) — the "Field Notes" design system every surface uses
- [deploy/README.md](deploy/README.md) · [deploy/aws/README.md](deploy/aws/README.md) — hosting and AWS operations

## The pieces

| Piece | What it does |
| --- | --- |
| **PE analyst portfolio** (`/signin/analyst/`) | Firm-scoped view over all portfolio companies: attention feed, opportunities with calculations and evidence, tasks, imports, agents |
| **Company workspace** (`/signin/`) | One acquired company: data sources and imports, findings triage, agents, run traces, spend |
| **Agent suite** (`src/vista/agents/`) | File Reviewer, Sector Merger, Report Generator, Recording Reviewer — durable jobs with per-call cost tracking and eval harness |
| **Desktop recorder** (`src/recorder/`) | Electron task-mining: on-device analysis, employee review of every AI explanation, only cleaned reports upload |
| **Backend** (`src/vista/`) | FastAPI + Postgres (schema-per-tenant isolation) + S3; durable job queue; append-only run/audit trail |

## Quickstart (local)

```sh
docker compose up -d                                # Postgres + MinIO
uv sync && cp .env.example .env
uv run python -m vista.manage migrate               # schemas + bucket
VISTA_COOKIE_SECURE=false uv run uvicorn vista.main:app --reload
uv run python -m vista.jobs.worker                  # separate terminal (agent jobs)
uv run pytest && npm test
```

Provisioning is operator-only: `python -m vista.manage create-workspace | provision-firm | seed-portfolio-demo`
(public `POST /tenants` stays disabled unless a provisioning key is configured).
In AWS, run the same commands via `deploy/aws/manage.sh`.

The `.agents/skills/vista-api-e2e-testing` skill documents the full local E2E recipe,
including live-model runs and spend controls.
