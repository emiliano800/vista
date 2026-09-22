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
- Company imports and the File Reviewer trigger (`/api/deals/{deal}/imports…`, `/records`, `/review`) need the tenant to be a portfolio company (`platform.firm_companies` with `deal_id`); a bare `POST /tenants` workspace gets 409 there. Create the company through the analyst API (`POST /api/portfolio/companies`) or `seed-portfolio` / `load-synthetic`, then add the employee with `add-user`.

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
- Those are the legacy v1 routes. A connected recorder uses protocol 2: `POST /api/recorder/submissions`, signed `upload-urls`, `complete` (queues `analyze_submission`), `answers`, `publish`; published reports list at `GET /api/recorder/reports` and on `/account/?view=recordings` (and read-only at `GET /api/companies/{id}/reports` for the analyst).
- Clearly distinguish a synthetic portable fixture from a genuine desktop recorder capture.

## File Reviewer, employee proxy and summary
- For dashboard imports, use `src/web/public/demo/simple/invoices.csv` (or the analyst wizard's Cedar files). Import data -> Confirm mapping -> Approve import writes canonical rows with provenance; then **Review imported records** queues a `canonical_review` File Reviewer run (worker required; stub model without a key). Set `VISTA_DEMO_TODAY` for reproducible overdue findings.
- POST `/synthetic/discovery` and inspect `/runs/{id}`, `/findings`, and `/usage`. Check ordered events, matching tool/model calls, finding IDs, and file/company evidence.
- Separate `discover.deterministic_facts(profile)` from additional model facts. A successful run with only deterministic findings may conceal unusable model output.
- Compare interpretations with actual profiles: repeated categorical values are not automatically defects, and small blank ratios need not be “high.”
- Employee creation requires `role_title`, not `role`. Create employee -> agent with `employee_id`, `schedule`, `scopes` -> agent run. Role-based hypotheses are not observations of actual work.
- POST `/summaries` after findings exist; verify `/summaries/latest` links to its run and preserves uncertainty rather than promoting hypotheses to verified facts.
- Do not equate employee discovery with a true Sector Merger/Analyze implementation; check current API/UI support.
- Dashboard Findings show open import exceptions plus every agent finding in the tenant ledger (the same rows the analyst snapshot returns); synthetic runs, employee runs, summaries and usage are also visible there or through the API. Check actual navigation and data sources before claiming integration.

## Computer Use Agent
- Needs an approved sandbox `workflow_versions` row (tenant `admin` decides via `POST /workflows/{w}/versions/{v}/decision`) and `VISTA_TYPESAFE_API_KEY` on the worker; stub Jev answers `none`, so the run pauses at step 1 with zero actions — that is the expected stub behaviour, not a passing execution.
- Check `GET /workflows/{w}/versions/{v}/eligibility` first: `execution_available` must be true; `availability.reasons` names what is missing (`harness_not_connected` → start the recorder in `--demo`, connect the company, open Computer use).
- Start with `POST /workflows/{w}/versions/{v}/runs {"mode":"sandbox"}`; poll `GET /workflow-runs/{id}`. Status order to expect: `queued → waiting_for_harness → running ⇄ waiting_for_human → succeeded|failed|stopped`. A documents-only workflow skips `waiting_for_harness`.
- `waiting_for_human` carries `pending.candidates` with Jev's probabilities and `pending.value_from` (an input *name*). Decide with `POST /workflow-runs/{id}/decision {"step_id":…, "decision":"approve"|"deny"}`; `POST …/stop` at any time. Verify one `usage_events` row per judgment and one `tool_call` event per step on `GET /runs/{agent_run_id}`; the final `findings` row has `finding_type=workflow.execution` and `evidence.verification`.
- Demo destination: `python3 -m http.server 8765` in `synthetic_data/front_end_work`, open `_sandbox/entry.html?reset=1`; diff its Export CSV against the Ridgeway `supplier_invoices.csv` rows the run used. This build's recorder answers browser/desktop steps `harness_unsupported` (placeholder harnesses), so a browser workflow pauses for a person after the first remote step.

## Evidence
- Workers consume a shared queue unless database-isolated: old recording/import jobs may fail or consume live tokens independently. Correlate logs by job/run ID.
- Pretty-print Chrome JSON pages and zoom using Ctrl+= for legible recordings.
- Preserve actual browser-fetched responses; remove credentials and signed storage URLs before sharing artifacts.

## Devin Secrets Needed
- None for local stub-mode testing; generate an ephemeral local `VISTA_PROVISIONING_KEY`.
- Live inference requires the session secret `VISTA_OPENAI_API_KEY`.
