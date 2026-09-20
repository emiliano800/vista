---
name: vista-local-api-e2e
description: Run Vista FastAPI and worker end-to-end locally with isolated tenants, live or stub models, and browser evidence.
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

## Live inference and spend control
- Obtain explicit permission and a small budget before live model calls. Pass `VISTA_OPENAI_API_KEY` securely to BOTH FastAPI and worker; never log the value.
- Use a new local database through `VISTA_DATABASE_URL` and run platform migrations before provisioning. An isolated tenant alone does not isolate the worker's platform job queue.
- Set the same database/model/base URL on both processes. Clear cassette settings so cached results cannot masquerade as live calls.
- Record GET `/usage` before and after each flow. Verify actual returned model IDs and `source: live` where available; environment configuration alone is not proof.
- Start `uv run python -m vista.jobs.worker` only when ready, process one small run per requested flow, and stop that isolated worker afterward.
- Reconcile model-call token totals with usage rows. Recording reviews may store model/tokens only on `RecordingReviewItem`, without a `UsageEvent` or `AgentRun`; report any missing API spend and separately label a read-only DB-derived estimate.
- Never replay a model invocation solely to improve screenshots.

## Recording Reviewer
- Use a valid portable `RecordingUpload` bundle and a real readable CSV/TXT/PDF attachment. Validate fixture shapes with `RecordingUpload` and `SectionsIn` before requests.
- POST `/deals/{deal_id}/recordings`, request signed media upload URLs using the exact document snapshot path, PUT actual file bytes, then POST `/recordings/{id}/media/complete`.
- PUT `/recordings/{id}/review/sections` to enqueue explanation separately from extraction.
- Check worker job status, `/recordings/{id}/files`, extracted text, `/review`, and `/evidence`. A queued upload alone is not successful extraction.
- At `/account/recordings/`, open View report and verify AI explanation, confidence, review state, and source steps.
- Clearly distinguish a synthetic portable fixture from a genuine desktop recorder capture.

## File Reviewer, employee proxy and summary
- For dashboard imports, use a small invoice CSV with `invoice_id`, `balance`, `due_date`, and `payment_plan`. Import data -> Review field mapping -> Confirm & analyze -> Review evidence.
- Set a fixed as-of date for reproducible overdue-invoice findings. This path is deterministic; it is not a live model call.
- POST `/synthetic/discovery` and inspect `/runs/{id}`, `/findings`, and `/usage`. Check ordered events, matching tool/model calls, finding IDs, and file/company evidence.
- Separate `discover.deterministic_facts(profile)` from additional model facts. A successful run with only deterministic findings may conceal unusable model output.
- Compare interpretations with actual profiles: repeated categorical values are not automatically defects, and small blank ratios need not be “high.”
- Employee creation requires `role_title`, not `role`. Create employee -> agent with `employee_id`, `schedule`, `scopes` -> agent run. Role-based hypotheses are not observations of actual work.
- POST `/summaries` after findings exist; verify `/summaries/latest` links to its run and preserves uncertainty rather than promoting hypotheses to verified facts.
- Do not equate employee discovery with a true Sector Merger/Analyze implementation; check current API/UI support.
- Dashboard Findings may be import-analysis-only, with synthetic findings, employee runs, summaries, and usage available only through API. Check actual navigation and data sources before claiming integration.

## Evidence
- Workers consume a shared queue unless database-isolated: old recording/import jobs may fail or consume live tokens independently. Correlate logs by job/run ID.
- Pretty-print Chrome JSON pages and zoom using Ctrl+= for legible recordings.
- Preserve actual browser-fetched responses; remove credentials and signed storage URLs before sharing artifacts.

## Devin Secrets Needed
- None for local stub-mode testing; generate an ephemeral local `VISTA_PROVISIONING_KEY`.
- Live inference requires the session secret `VISTA_OPENAI_API_KEY`.
