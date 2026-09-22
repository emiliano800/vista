# Deploy the Vista platforms

Vista's product direction is a financial platform for PE analysts and portco CFOs,
plus an FDE platform for workflow automations and operational results. The CFO sees
the company-scoped subset of the analyst's financial view. FDEs work within assigned
companies and workflows; their results provide evidence for financial impact, with
estimates kept separate from realized benefits.

Today, the web app has a firm-scoped analyst workspace and an existing company
workspace. Dedicated CFO/FDE views and their permissions are pending. These product
boundaries do not currently imply separate servers, domains, or databases: the
existing deployment uses one web app/proxy and a shared API with tenant isolation.
The analyst's `/company/` page still uses firm-wide access and must not be treated
as a restricted CFO portal.

## Components

* Cloudflare Worker `vista`: static browser UI and same-origin `/api` proxy.
* Python API: company and firm permissions, canonical imports, financial metrics,
  findings, tasks, agent runs, hashed access keys, and expiring browser sessions.
* Postgres 16: isolated tenant records plus shared firm memberships and job queue.
* Private S3-compatible storage: imported source files, recording reports, and
  submitted recording media/documents; see [recorder behavior](../src/recorder/README.md).
* Job worker: durable company review, portfolio interpretation, and recording jobs.

Cloudflare's static hosting alone cannot run this FastAPI/Postgres stack. Deploy the
backend either to AWS (recommended: ECS Express Mode + RDS + S3, see
[aws/README.md](aws/README.md)) or to a single Docker server as described below. The
backend needs a public HTTPS URL before connecting the Worker. The existing
`bumpsolutions.org` custom domain stays attached to the `vista` Worker.

## Role separation when deploying

Current entry points are `/signin/analyst/` for the firm portfolio and `/signin/`
for the company workspace. Neither provisioning a company user nor giving someone
AWS access creates the planned CFO or FDE application role.

Before releasing those views, enforce CFO company scope and FDE assignments in API
queries, evidence downloads, exports, and job/run access. Reuse financial calculations
between CFO and analyst views; never send a firm-wide snapshot and filter it only in
the browser. A dedicated FDE workspace must distinguish proposed automation, approved
work, execution status, and measured results. Existing agent traces alone do not
prove that a business workflow has been automated.

## Local end-to-end run (recording evidence)

```sh
uv sync --group dev
docker compose up -d
uv run python -m vista.manage migrate
uv run python -m vista.manage create-workspace --firm "Your firm" --company "Your company" --email "you@example.com"
VISTA_COOKIE_SECURE=false uv run uvicorn vista.main:app --host 127.0.0.1 --port 8000
uv run python -m vista.jobs.worker      # second terminal: AI review of recordings (needs VISTA_OPENAI_API_KEY)
```

The provisioning command prints a personal access key once. Store it in your password
manager. Open `http://127.0.0.1:8000`, sign in with that key and copy the company ID.
In the desktop recorder's Settings → Cloud workspace, enter that URL, the ID and the
same key. Connect, record a session, stop and wait for analysis, then choose **Upload
report** in My recordings. Refresh the website to see it. An empty workspace stays
empty until you upload a report. Use `npm start -- --dashboard` in `src/recorder` to
launch the recorder; its existing OS capture permissions still apply.

## One-server backend with HTTPS

1. Use a Linux server with Docker Compose, ports 80/443 open, and DNS for
   `api.bumpsolutions.org` pointing to it. Keep database and object-storage ports
   private; the supplied production Compose file does not publish them.
2. Copy `deploy/.env.example` to `deploy/.env`, replace both passwords with separate
   random hex values, and set `API_HOST`. The file is ignored by Git and Docker.
3. Start the stack from the repository root:

```sh
docker compose --env-file deploy/.env -f deploy/compose.yml up -d --build
docker compose --env-file deploy/.env -f deploy/compose.yml exec api .venv/bin/python -m vista.manage create-workspace --firm "Your firm" --company "Your company" --email "you@example.com"
```

The migration service upgrades the shared and existing tenant schemas and creates the
private report bucket before the `api` and `worker` services start. The worker processes
agent jobs and recording AI review; set `OPENAI_API_KEY` in `deploy/.env` for live model calls.
Caddy obtains/renews the HTTPS certificate.
Check `https://api.bumpsolutions.org/api/health`. Persist and back up the Postgres,
report-storage and Caddy volumes. `down -v` deletes that data; do not use it for updates.

For another managed host, build the root Dockerfile, set `VISTA_DATABASE_URL` (or the
`VISTA_DB_HOST` / `VISTA_DB_USER` / `VISTA_DB_PASSWORD` parts, which the app composes and
URL-escapes), `VISTA_S3_BUCKET`, `VISTA_S3_ENDPOINT_URL` + keys (leave empty on AWS to use
the instance/task IAM role), and `VISTA_ALLOWED_ORIGINS` (a JSON array of the two web
origins). Set `VISTA_MIGRATE_ON_START=true` or run `.venv/bin/python -m vista.manage migrate`
as the release command before starting the API. Use a private bucket and credentials scoped
to that bucket. `VISTA_COOKIE_SECURE` defaults to true; only disable it for local HTTP
development. Recording reports upload without OpenAI credentials or the job worker; the
AI review of a recording (`explain_recording` jobs, `GET /api/recordings/{id}/review`)
needs both `VISTA_OPENAI_API_KEY` and a running worker (`python -m vista.jobs.worker`).
Without a key the worker still runs and marks every stretch as needing the employee's own
explanation.

## Connect Cloudflare

In Worker `vista` → Settings, add the plain-text variable `API_ORIGIN` with your
backend origin, for example `https://api.bumpsolutions.org` (no `/api` suffix).
Set Builds → Deploy command to:

```sh
npx wrangler deploy
```

Set the preview command to `npx wrangler versions upload` and leave the build command
blank. The old command pointing at `./company-portal` must be replaced. The root
`wrangler.jsonc` now defines the Worker script and `src/web/public` assets. Deploy
only after the API health check succeeds. `API_ORIGIN` is configured in the dashboard,
not in source; `keep_vars` preserves it when deploying from Git. Preview deployments
must use an explicitly allowed origin or a separate test backend.

The Worker forwards an explicit allow-list of authentication, portfolio, import,
agent, company, and recording routes (`src/web/worker.mjs`). New CFO/FDE API routes
must be added to that allow-list when implemented. It strips
unneeded client headers, rejects foreign-origin mutations and redirects, sends no-store
responses for private data, and sets a restrictive content-security policy. Browser
keys are exchanged for HttpOnly cookies and are never put in localStorage. Desktop
keys are encrypted through the OS keychain. If the keychain is unavailable, connecting
fails rather than saving a plaintext key.

## User administration and updates

Use the commands on the backend host (prefix with the Compose `exec api` command above
when using Docker):

```sh
.venv/bin/python -m vista.manage add-user --tenant TENANT_UUID --company COMPANY_UUID --email employee@example.com --role member
.venv/bin/python -m vista.manage add-user --tenant TENANT_UUID --company COMPANY_UUID --email analyst@example.com --role viewer
.venv/bin/python -m vista.manage rotate-key --user USER_UUID
```

These are existing company membership roles, not CFO/FDE persona assignments.
Firm membership separately controls the analyst APIs; a company viewer is not a
PE analyst simply because their email or UI label says "analyst".

Members upload; viewers only read. Both require company membership. Key rotation
invalidates old bearer keys and all browser sessions. Public `POST /tenants` is disabled
unless the optional `VISTA_PROVISIONING_KEY` is set; use the operator CLI instead.
Existing access keys remain valid after migration, but only their digests remain in
Postgres. This migration cannot recover plaintext keys on downgrade; back up the DB
before upgrading and rotate a key if its owner has lost it.

Reuploading the same session by the same user to the same company is idempotent.
After editing local notes, wait for reanalysis and upload again to replace the visible
snapshot. Previous content-addressed blobs remain in storage; retention/deletion
administration, automatic background syncing, password/email login and browser screen
recording are outside this first version. Uploads are capped at 8 MiB and 50,000 steps;
larger sessions must be shortened. CSV exports neutralize spreadsheet formulas; JSON
retains the exact uploaded evidence. Reports still contain business information (labels,
case IDs and notes); review before sharing.

## Verify

```sh
uv run pytest -q
npm ci
npm test
npx wrangler deploy --dry-run
uv run ruff check .
uv run ruff format --check .
```

Backend integration tests require local Postgres and exercise real migrations and tenant
schemas, with a fake S3 transport for deterministic outage/retry tests. For a release,
also upload a completed report against the real S3 backend, sign in through the website,
inspect evidence, sign out, and verify another company's key cannot retrieve the report.

Configuration references: [Cloudflare Wrangler](https://developers.cloudflare.com/workers/wrangler/configuration/),
[Electron safeStorage](https://www.electronjs.org/docs/latest/api/safe-storage).
