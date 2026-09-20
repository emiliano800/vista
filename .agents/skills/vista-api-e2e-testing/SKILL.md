---
name: vista-local-api-e2e
description: Run Vista FastAPI and worker end-to-end locally with isolated tenants and browser evidence.
---

# Local API and worker testing

Run from the Vista repository root so migrations and synthetic data resolve.

## Services
- Use `docker compose up -d` for Postgres and MinIO; check `docker compose ps`.
- Dependencies are installed by `uv sync --group dev`.
- Run `uv run python -c 'from vista.tenancy import migrate_platform; migrate_platform()'`.
- Start `uv run uvicorn vista.main:app --host 0.0.0.0 --port 8000`.
- For local HTTP set `VISTA_COOKIE_SECURE=false`. Configure a randomly generated local `VISTA_PROVISIONING_KEY` for both the API process and provisioning client.
- To guarantee stub inference, clear `VISTA_OPENAI_API_KEY` and `VISTA_LLM_CASSETTE` in both API and worker environments. Settings can also load `.env`, so verify effective settings.
- `make start` launches the desktop recorder, not FastAPI; `make run` launches the taskmining engine, not the API.

## Isolated accounts and browser access
- POST `/tenants` using `X-Vista-Provisioning-Key` and `{name, owner_email}`. Store returned `api_token` privately.
- Create two fresh tenants for isolation checks rather than reusing populated tenants.
- `/signin/` accepts the access key and creates a browser session. `/account/` is the web dashboard.
- `/docs` exposes Swagger. Authorize HTTPBearer with the raw access key to exercise API-only POST endpoints.
- Cookie-authenticated writes require `X-Vista-Request: 1`; same-origin browser fetch is appropriate for structured API assertions. Do not extract browser cookies into shell commands.
- If no user-management API exists, create a local test `vista.models.platform.User` with `role="member"` in the new test tenant to exercise admin restrictions.
- Dashboard companies use `/deals`. A synthetic manifest company is not automatically a dashboard deal.

## Discovery evidence
- With worker stopped, POST `/synthetic/discovery`, save the returned ID and queued response.
- Start `uv run python -m vista.jobs.worker`, then inspect `/runs/{id}`, `/findings`, `/usage`.
- Check event order, matching tool/model calls, finding IDs, evidence paths/company, and token/cost totals against the actual tables processed.
- Workers consume the shared queue: old recording/import jobs may fail independently of the tested job. Correlate logs by job/run ID; stop the test worker when done.
- Pretty-print Chrome JSON pages and zoom using Ctrl+= for legible recordings.
- Dashboard Findings may be import-analysis-only. Confirm its API sources before claiming synthetic agent results are displayed.

## Devin Secrets Needed
None for local stub-mode testing. Generate an ephemeral local `VISTA_PROVISIONING_KEY`; live model tests separately require `VISTA_OPENAI_API_KEY`.
