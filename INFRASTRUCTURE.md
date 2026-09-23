# Vista — Infrastructure Primer

How Vista runs, end to end, with this project's real names. For onboarding: read
this top to bottom once, then keep the runbook section handy.
(Product-level docs: `README.md` → `CURRENT_IMPLEMENTATION.md`.)

## The big picture

```
                       ┌───────────────────────────────────────────────┐
 browser / recorder →  │  Cloudflare Worker "vista"  (bumpsolutions.org)│
                       │  serves src/web/public assets                  │
                       │  proxies an explicit /api allow-list           │
                       └───────────────┬───────────────────────────────┘
                                       │ https (API_ORIGIN)
                       ┌───────────────▼───────────────────────────────┐
                       │  AWS · account 630396228214 · us-east-1       │
                       │  CloudFormation stack: vista                   │
                       │                                                │
                       │  ECS Fargate cluster "vista"                   │
                       │   ├─ vista-api     FastAPI (1–4 tasks, ALB)    │
                       │   └─ vista-worker  agent-job worker (1 task)   │
                       │        │  polls platform.jobs (SKIP LOCKED)    │
                       │        ▼                                       │
                       │  RDS Postgres "vista-postgres" (db.t4g.micro)  │
                       │   platform schema + one schema per tenant      │
                       │  S3 "vista-reports-630396228214"               │
                       │   private, versioned: reports, media, imports  │
                       │  Secrets Manager: DB master secret,            │
                       │   vista/openai-api-key (model: gpt-6-astra)    │
                       │  CloudWatch: /vista/vista/api, /vista/vista/worker │
                       └───────────────────────────────────────────────┘
```

- One template defines everything: `deploy/aws/template.yaml`.
- Public API endpoint (behind Cloudflare):
  `https://vi-6526b1efec4446e48c627173e9e805ce.ecs.us-east-1.on.aws`
- RDS is **not** publicly reachable. All DB access goes through the API or one-off
  ECS tasks. There is no SSH anywhere; use `aws ecs execute-command` if you truly
  need a shell in a task.

## Component notes

**Cloudflare Worker (`src/web/worker.mjs`, config `wrangler.jsonc`)** — serves the
static site and forwards only an allow-listed set of `/api` routes with method
gating, size caps (8 MiB), origin checks and strict security headers. It deploys
automatically from Git pushes (`npx wrangler deploy`); `API_ORIGIN` is a dashboard
variable preserved by `keep_vars`. ⚠ A new backend route is invisible in
production until added to the worker's allow-list — the classic "works locally,
404 live" trap.

**API + worker (one Docker image)** — built from the repo `Dockerfile` (includes
`src/`, `migrations/`, `synthetic_data/`). `docker-entrypoint.sh` picks the role:
API by default, worker via `VISTA_ROLE`, or an explicit command for one-off tasks.
On start (when `VISTA_MIGRATE_ON_START=true`) it runs Alembic migrations under a
Postgres advisory lock so concurrent task launches don't race. The worker executes
agent jobs (File Reviewer, Sector Merger, Report Generator, Recording Reviewer,
Computer Use Agent, extraction) from the `platform.jobs` queue — no Redis/SQS; the
queue is Postgres. The worker never reaches an employee's computer: browser/desktop
steps are pulled by the recorder over the same `/api` proxy.

**Database** — schema-per-tenant isolation: `platform` (tenants, users, sessions,
jobs, firm layer) plus one `t_<hex>` schema per company. Two Alembic environments
(`alembic -n platform`, `-n tenant` applied per schema). Deletion protection is on.

**Storage** — S3 bucket with versioning and full public-access block. Content-
addressed keys per tenant: recording bundles, media snapshots, extracted document
JSON, uploaded import files. Tasks use the IAM task role (no static S3 keys in prod).

**Secrets** — DB credentials are an RDS-managed secret; the OpenAI key lives in
`vista/openai-api-key`, injected as `VISTA_OPENAI_API_KEY`. Nothing secret is in
the image or repo; local `.env` / `deploy/aws/.env` are gitignored.

## Environments

| | Local | Production |
| --- | --- | --- |
| DB | `docker compose` Postgres 16 | RDS `vista-postgres` |
| S3 | MinIO (`quay.io/minio/minio`) | S3 + IAM role |
| Model | stub unless `VISTA_OPENAI_API_KEY` set | gpt-6-astra via Secrets Manager |
| Frontend | FastAPI serves `src/web/public` | Cloudflare Worker |
| Provisioning | `python -m vista.manage …` | `deploy/aws/manage.sh …` (one-off Fargate task) |

## Deploy pipeline

1. **Sole source of truth: `emiliano800/vista` `main`.** Keep code, documentation,
   and deployment artifacts in this repository. Deploy only an image containing
   the database's complete migration history.
2. `deploy/aws/deploy.sh` (reads `deploy/aws/.env`): builds the image → pushes to
   ECR `vista-api` → `aws cloudformation deploy` with parameters. Rolling update;
   the site stays up. Typical run: 10–15 min.
3. Requires a **deployment-capable identity**. The machine-default
   `emiliano-vista-operator` IAM user deliberately cannot deploy (no
   ECR push / UpdateStack) — it's for management tasks and logs only.
4. Frontend ships separately and automatically: Cloudflare rebuilds on every push.
5. CI (`.github/workflows/ci.yml` in this repository): ruff lint+format, Python suite
   against a Postgres service container, taskmining run, recorder/web JS tests,
   wrangler dry-run. There is **no auto-deploy** — deploys are manual on purpose.

### The one deploy rule that has already bitten us

**Never deploy an image whose migrations are older than the database head.**
Startup migration fails with `Can't locate revision …`, health alarms fire, and
CloudFormation wedges in rollback (2026-09-20). Forward-fix: deploy a newer image;
if the stack is stuck, `aws cloudformation continue-update-rollback` (admin).

## Access model

| Tier | Can | Cannot | Who |
| --- | --- | --- | --- |
| Operator | describe stack, tail logs, run `manage.sh` tasks (provision/rotate/seed/migrate) | deploy, touch IAM, read secrets directly | `emiliano-vista-operator` (Keychain-stored key), one per teammate |
| Deployer | everything operator + ECR push, CloudFormation update, pass task roles | — | admin identity, 1–2 people |

Issued workspace keys are delivered with `manage.sh --output-file PATH …` so they
never land in CloudWatch. Demo keys in `DEMO_ACCESS.md` are public **only because
the data is synthetic** — rotate all keys (incl. OpenAI) before real customers.

## Runbook

```sh
# health
curl -s https://bumpsolutions.org/api/health
aws cloudformation describe-stacks --stack-name vista \
  --query "Stacks[0].StackStatus" --output text
aws ecs describe-services --cluster vista --services vista-api vista-worker \
  --query 'services[].{name:serviceName,desired:desiredCount,running:runningCount}'

# logs
aws logs tail /vista/vista/api --follow
aws logs tail /vista/vista/worker --follow

# management task (example: seed the demo portfolio)
deploy/aws/manage.sh --output-file /tmp/key.json seed-portfolio

# redeploy backend (deployer identity + Docker running)
deploy/aws/deploy.sh

# shell into a running task (Session Manager plugin required)
aws ecs execute-command --cluster vista --task <task-id> \
  --container Main --interactive --command /bin/sh
```

Cost picture (rough): 2 always-on Fargate tasks (0.5 vCPU / 1 GB each),
`db.t4g.micro` + 20 GB storage, S3/logs pennies, Cloudflare free tier → low tens of
$/month; model spend is metered per call in `usage_events` (gpt-6-astra:
$10/M input, $50/M output — the dominant variable cost).
