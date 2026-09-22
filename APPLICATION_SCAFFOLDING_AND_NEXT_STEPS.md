# Vista — Application Scaffolding and Next Steps

## Start here

**Current priority: the employee recorder pipeline.** Workflow execution is deferred
while we connect the app to the right workspace and accept uploads without requiring
Python on the employee's computer.

| Area | Current repository status |
| --- | --- |
| Workflow registry, immutable versions, approvals | Implemented and tested; no executor |
| Recorder step 1: personal-key connection and company selection | Implemented and tested |
| Recorder step 2: unprocessed-session upload, retry queue, verified private receipt | Implemented and tested |
| Recorder step 3: cloud analysis, clarification, report generation and publication | Not implemented for the new intake format; this is the next engineering step |
| Sandbox workflow execution | Deferred at the user's request |
| Learning / reinforcement learning (RL) | Optional stretch goal, not a delivery dependency |

The new recorder flow currently ends at **Uploaded — awaiting analysis**. A verified
upload is not a completed report or a published company finding. Existing report
and agent APIs remain separate from this new private intake path.

This document describes source code, not a fresh certification of the deployed AWS
image or downloadable installers. The changes require backend migration `0018`,
the updated Cloudflare Worker, and a newly built recorder installer. This update
does not itself deploy AWS or publish a desktop release.

Related documents: [current implementation](CURRENT_IMPLEMENTATION.md),
[product direction](NEXT_STEPS.md), [business context](BUSINESS_COURSE_OF_ACTION.md),
and [infrastructure](INFRASTRUCTURE.md).

## 1. Completed foundation: workflow registry and approvals

### 1. Store workflows and immutable versions

A workflow belongs to one canonical portfolio company and its tenant schema.
Creating it also creates version 1 as a draft. Each version records:

- Goal and required input names.
- Allowed tool names and success criteria.
- Environment, currently restricted to `sandbox`.
- Maximum steps, runtime, and model-cost budget.
- Creator, creation time, and a SHA-256 digest of the definition.

Edits append a new version. There are no APIs to update or delete historical
versions. Postgres triggers also reject ordinary UPDATE and DELETE operations on
version and approval rows. Tenant migration `0017_workflows` creates the three
tables: `workflows`, `workflow_versions`, and `workflow_approvals`.

The tool names, input names, and success criteria are declarations for a future
runner. They are not yet connected to tool implementations or executable verifiers.
Budget fields are stored and validated, not enforced against live execution yet.

### 2. Approve or reject an exact version

The approval record binds the decision to the version ID and definition digest,
with the reviewer ID, timestamp, and reason. One final decision is allowed per
version. Retrying the same decision and reason is idempotent; a conflicting decision
returns 409. To revise a rejected or approved definition, create a new version.

Only the latest version may receive a decision. A new draft immediately supersedes
the previous version for eligibility, even if the previous version was approved.
The old definition and decision remain readable as history. This is intentionally
conservative; a future runner may need a separate active-version promotion policy.

Version creation requires `expected_version`, the latest number the editor read.
Stale edits return 409 rather than overwriting another change. Row locks serialize
competing edits and review decisions.

### 3. Check eligibility and test the boundaries

The read-only eligibility check returns `eligible: true` only when:

- The caller resolves to an authorized company through current firm membership.
- The caller has the existing firm `admin` or `operator` role.
- The requested version is the latest version.
- That exact version is approved.
- Its definition and approval digests match, and its environment is `sandbox`.

Every response also includes **`execution_available: false`**. Eligibility means
that the registry's approval gate passes, not that anything can run. No jobs are
enqueued, no model calls are made, and no business systems are changed.

A future executor must re-check authorization, approval, connectors, and limits
when it actually runs. A previously returned eligibility response is not a durable
execution credential.

## 2. Permissions in this first version

This milestone uses existing firm memberships rather than inventing unfinished
CFO or FDE roles.

| Existing firm role | Read company workflows/history | Create drafts/versions | Approve/reject | Pass eligibility when latest version is approved |
| --- | --- | --- | --- | --- |
| admin | Yes | Yes | Yes | Yes |
| operator | Yes | Yes | No | Yes |
| analyst | Yes | Yes | No | No |
| viewer | Yes | No | No | No |

All actions are restricted to companies linked to that member's firm. Each lookup
also checks that the workflow belongs to the company in the URL and the version
belongs to that workflow. A different firm's IDs or a workflow addressed through
the wrong company return 404. A user without firm membership cannot use these APIs,
even if their separate platform user role is admin.

Important boundaries:

- Current firm membership grants access to that firm's companies; there are no
  per-workflow FDE assignments in this milestone.
- `operator` is an existing permission level, not a new FDE role.
- A firm admin can approve their own draft. Four-eyes review is not implemented.
- Decisions are final per version; there is no approval-revocation endpoint yet.
  Creating a new draft makes the prior version ineligible.
- Company, financial, and workflow permissions must be separated before exposing
  dedicated CFO/FDE experiences. Hiding navigation is not authorization.

## 3. API contract

All paths below are relative to `/api`; the API also registers the corresponding
unprefixed routes. `company_id` is a canonical firm-company UUID or slug, not a
legacy recording `deal_id`. Workflow and version IDs are UUIDs.

Let `BASE = /api/companies/{company_id}/workflows`:

| Method and path | Purpose |
| --- | --- |
| `POST BASE` | Create a workflow and its first draft version |
| `GET BASE` | List workflows with their latest version |
| `GET BASE/{workflow_id}` | Read the workflow and its latest version |
| `POST BASE/{workflow_id}/versions` | Append a new draft using `expected_version` |
| `GET BASE/{workflow_id}/versions` | Read immutable version/decision history, newest first |
| `GET BASE/{workflow_id}/versions/{version_id}` | Read one version and its decision |
| `POST BASE/{workflow_id}/versions/{version_id}/decision` | Approve or reject the latest version |
| `GET BASE/{workflow_id}/versions/{version_id}/eligibility` | Evaluate the approval gate for the current caller |

Lists support `limit` (1–100, default 50) and nonnegative `offset`. Unknown input
fields, empty required values, duplicate list entries, invalid limits, and a
production environment are rejected. PUT/PATCH/DELETE and workflow run routes
are not provided. The [Cloudflare proxy](src/web/worker.mjs) exposes only the
registry's permitted read and POST operations.

### Example: create an invoice-draft workflow

Request body for `POST BASE`:

```json
{
  "name": "Supplier invoice drafts",
  "definition": {
    "goal": "Prepare a supplier invoice draft for review",
    "required_inputs": ["invoice", "supplier_directory"],
    "allowed_tools": ["read_invoice", "lookup_supplier", "create_invoice_draft"],
    "success_criteria": [
      "The draft matches the invoice number, supplier, currency and total"
    ],
    "environment": "sandbox",
    "limits": {
      "max_steps": 10,
      "max_runtime_seconds": 300,
      "max_cost_usd": "1.00"
    }
  }
}
```

The response includes `latest_version.id`, `latest_version.number`, the normalized
definition, its hash, and `status: "draft"`. Decimal costs are serialized as strings.

To edit, POST a new full `definition` with `expected_version: 1` to the versions
collection. The result is version 2, still a draft, with no inherited decision.

An authorized admin approves or rejects with:

```json
{
  "decision": "approved",
  "reason": "Reviewed the sandbox-only scope and draft creation goal"
}
```

Use `"rejected"` for rejection. The server records the reviewer and timestamp;
clients cannot supply or spoof them. Repeating the same decision preserves the
original reviewer and time.

## 4. Code and verification

| Area | Implementation |
| --- | --- |
| Validated definitions and API response types | [automation/schemas.py](src/vista/automation/schemas.py) |
| Versioning, decision records, eligibility | [automation/service.py](src/vista/automation/service.py) |
| Authenticated company-scoped routes | [api/workflows.py](src/vista/api/workflows.py) |
| Workflow, version, and approval models | [models/tenant.py](src/vista/models/tenant.py) |
| Tables and immutable-history triggers | [tenant migration 0017](migrations/tenant/versions/0017_workflows.py) |
| API and database regression tests | [test_workflows.py](tests/test_workflows.py) |
| Public proxy route tests | [worker.test.mjs](src/web/test/worker.test.mjs) |

The regression suite checks draft/approval/rejection, concurrent edits and decisions,
stale-version handling, unchanged historical definitions, cross-firm and wrong-company
access, role restrictions, invalid inputs, current-role eligibility, and database
immutability. These are registry tests, not evidence that external automation works.

For local development, use the normal local Postgres/MinIO configuration, then:

```sh
uv run python -m vista.manage migrate
uv run pytest -q tests/test_workflows.py
node --test src/web/test/worker.test.mjs
```

The focused implementation tests were run against a separate local test database
with model calls disabled. No production migrations or model calls are required to
exercise this milestone. For deployment, ship the backend and tenant migration
before clients depend on the new routes; update the Worker allow-list with it.

## 5. Deferred: one verified sandbox action

This work has not been implemented and is not the current priority. When workflow
execution resumes, the first slice should be:

```text
Authorized invoice
  -> approved workflow version
  -> permitted sandbox tool
  -> draft creation
  -> independent read-back verification
  -> human correction, if needed
```

Use a deterministic connector simulator for repeatable failure tests, then the
provider's real sandbox. Start with one invoice format and one accounting destination.
Keep payments, real ledger posting, autonomous outreach, and arbitrary computer use
outside that milestone.

Required additions, not implemented yet:

1. **Typed tools and a company-bound connection.** Define validated arguments,
   permissions, external account binding, secret references, and tool results.
   Stored `allowed_tools` labels alone do not grant a connector permission.
2. **Execution safety.** Add worker leases/recovery, atomic run/job creation,
   action checkpoints, idempotency keys, budget enforcement, and reconciliation.
   A timeout after draft creation must not create a second draft on retry.
3. **Independent verification and ordinary feedback.** Read the resulting record
   and compare company, supplier, invoice number, currency, amounts, and attachment.
   Save outcome/correction evidence against the workflow version and run.

Reuse `AgentRun`, events, usage, S3 artifacts, and canonical record provenance rather
than creating a disconnected execution ledger. Do not treat the current
[executor phase](src/vista/agents/execute.py), which returns action dictionaries,
as a connector implementation. Likewise, the legacy portfolio `run_agent_now()`
display path must not stand in for an actual queued execution.

## 6. Current milestone: recorder connection and private uploads

### Step 1 — employee connection: implemented

The connection panel is available without `VISTA_ADMIN=1`. On first launch without
a current connection, the app opens its dashboard; Settings offers:

1. Enter a personal Vista access key. The website defaults to bumpsolutions.org;
   an advanced override supports local development or another authorized deployment.
2. Fetch authorized workspaces from `GET /api/recorder/workspaces`.
3. Select a company when there are several; the app does not silently pick the first.
4. Confirm the recording/sharing notice and connect.

The access key is encrypted with Electron `safeStorage` before being saved locally.
The app records user, tenant, destination, and a persistent installation UUID. New
recordings started while connected retain that workspace binding, and their upload
cannot be silently moved to another account or destination. Existing older connection
files require reconnection through the new flow.

Workspace resolution is explicit:

- A `member` or `admin` user whose tenant is a canonical `FirmCompany` can upload to
  that company, unless it has exited.
- Otherwise, existing deal-based accounts can select their `member`/`owner` deals.
  These remain legacy destinations, with no automatic name-based mapping to a
  different canonical tenant.
- A firm analyst's portfolio visibility does not automatically grant employee-upload
  access to every canonical company. Appropriate company accounts must be provisioned.

This is personal-key enrollment, not SSO or device-token management. The installation
UUID is a label, not an authentication credential. Requests continue to use the
personal key and re-check current workspace access on the server.

### Step 2 — upload before analysis: implemented

The v2 intake flow does not require local Python, a checkout, `summary.json`, or a
processed event CSV. New connected/packaged clients can store a stopped session as
an unprocessed private submission:

```text
Stopped session
  -> preview destination and optional documents
  -> explicitly approve the sharing package
  -> persist a frozen package in the local upload queue
  -> create private submission and request signed upload URLs
  -> upload the approved artifacts
  -> server verifies sizes, hashes, and metadata schema
  -> receive an idempotent receipt: Uploaded — awaiting analysis
```

The default activity artifact contains only timestamps, app names, interaction
types, and counts. It omits window titles, URLs, recorded typed text, clipboard
contents, screenshots, video, and the original raw event file. Paused intervals and
sections excluded before queuing are omitted from the activity artifact.

Document snapshots are optional and unchecked by default. Selected documents upload
in full, not as redacted excerpts; the employee must review their contents first.
Section exclusions do not redact the contents of a separately selected document.

Current bounds: one activity artifact up to 4 MiB / 50,000 events, at most ten
documents up to 20 MiB each, and a 50 MiB total package. Supported document types are
CSV, TSV, TXT, PDF, XLSX, XLSM, DOCX, and PPTX. Paths and symlink escapes are rejected.

The disk-backed queue freezes the manifest and copies the selected bytes before
network work. It resumes when the app is open and connected to the original account,
renews signed URLs on retry, and skips already acknowledged artifacts. Disconnecting
pauses further work; account/workspace changes do not retarget pending packages.
Authentication and invalid-format errors pause automatic retries until an explicit
retry/reconnection. No access keys or signed URLs are written into the queue state.

S3 PUTs are bound to the declared content length and SHA-256 checksum. The API also
reads back and verifies the required objects before recording acceptance. Lost
completion responses can be recovered through the same submission and receipt.
Local recordings and queue copies are retained, including after successful upload.

The package is immutable once queued. The app blocks later changes to its sharing
selection/exclusions; local edits are not a withdrawal of previously uploaded data.
Cancellation, withdrawal, retention, and queue cleanup controls remain future work.

### Backend storage and API

Tenant migration `0018_recorder_submissions` adds the `recorder_submissions` table.
S3 holds artifacts; the tenant row holds the manifest/hash, owner, installation and
source IDs, resolved canonical company when present, upload state, and verification
receipt. Retries of the same source package return the same submission; an attempt
to replace that source with a different manifest is rejected.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/recorder/workspaces` | Resolve the current user's permitted upload destinations |
| `POST /api/recorder/submissions` | Register the v2 manifest with explicit sharing consent |
| `GET /api/recorder/submissions` | List the current uploader's accessible private submissions |
| `GET /api/recorder/submissions/{id}` | Inspect one owned submission and receipt |
| `POST /api/recorder/submissions/{id}/upload-urls` | Get short-lived, checksum-bound URLs for the approved artifacts |
| `POST /api/recorder/submissions/{id}/complete` | Verify objects and accept the upload idempotently |

Ownership is enforced even between two users of the same company. Losing workspace
access blocks subsequent operations. There is no public download, publish, analysis,
or workflow execution route for these private submissions.

The API currently reports `analysis_status: not_started` and
`publication_status: draft`; these are fixed states while the next processing stage
is absent. No analysis job is enqueued. New uploads do not appear as completed reports
or findings in the company/portfolio pages. The existing v1 completed-report APIs
remain available separately; do not assume their behavior has been replaced for
older installed clients.

### Implementation and verification

| Area | Code |
| --- | --- |
| Workspace resolution, manifest validation, signed URLs, receipt verification | [recorder_uploads.py](src/vista/recorder_uploads.py) |
| Owner-scoped intake API | [api/recorder.py](src/vista/api/recorder.py) |
| Tenant schema | [migration 0018](migrations/tenant/versions/0018_recorder_submissions.py) |
| Metadata packaging, scope binding, local retry queue | [intake.js](src/recorder/src/intake.js) |
| Desktop integration and connection lifecycle | [main.js](src/recorder/src/main.js) |
| Employee company picker and sharing preview | [dashboard.html](src/recorder/ui/dashboard.html) |
| API, privacy, receipts, and local object-store tests | [test_recorder_uploads.py](tests/test_recorder_uploads.py) |
| Queue and metadata-filter tests | [intake.test.js](src/recorder/test/intake.test.js) |
| Setup/review UI tests | [recorder-enrollment.test.mjs](src/web/test/recorder-enrollment.test.mjs) |

Verification for this implementation: 191 Python tests passed against a fresh local
Postgres instance, including a signed-upload round trip against local MinIO; 121
JavaScript tests passed; Ruff lint/format and the Worker dry-run passed. No live model
calls or AWS operations were used. Tests include cross-account denial, revoked access,
missing/corrupt artifacts, interrupted uploads, lost receipts, explicit document
consent, privacy exclusions, and the distinction between uploaded and analyzed.

A run against the long-lived local Postgres container hit shared-memory exhaustion;
the final suite passed in a separate fresh Postgres 16 container with 256 MiB shared
memory. No repository/database security settings were relaxed. A new database alone
does not isolate the server's memory resources from other local workloads.

Packaging now includes the shared theme/fonts, but a clean-machine installed-app
acceptance test and new published installers are still required. Renderer tests use
a mocked Electron bridge; they do not certify native capture permissions or an
installer rollout.

### Step 3 — cloud analysis and report publication: next, not implemented

1. Add a durable, idempotent analysis job for an accepted submission, using verified
   artifact references. Adapt the existing task-mining pipeline to the metadata-only
   format and make reduced observation coverage explicit; do not assume the removed
   window/clipboard/typed-text fields are available.
2. Extract approved documents and run backend interpretation with scoped inputs,
   metered model calls, retry/recovery, and bounded cost. Keep raw source records
   separate from inferred workflow findings.
3. Persist a draft report, surface focused employee questions, and add explicit
   publication permissions and a report/evidence view for the correct workspace.
   Define how legacy deal destinations connect to canonical company views without
   guessing by name or silently copying data across tenants.

Also still needed: dedicated device-token revocation/SSO if required, withdrawal and
retention controls, and production monitoring. Recording evidence is not an accounting
entry; suitable business documents still need mapping, validation, and import approval
before becoming canonical financial records.

### Rollout checklist (separate from implementation)

- Deploy the current backend image and apply migration `0018` using the existing AWS
  deployment process, then verify service stability and the new authenticated APIs.
- Deploy/verify the matching Cloudflare Worker allow-list.
- Build and publish a new recorder release; pushing source code does not update the
  already-downloadable installers. Test fresh installation and reconnection without
  a source checkout or Python installation.
- Do not promise generated reports until step 3 is implemented and verified. The
  successful end state of this release is a verified private upload awaiting analysis.

## 7. Stretch goal: learning and RL (next-next step)

**RL is optional future work, not a dependency of the registry, sandbox executor,
or recorder integration.** First prove that a bounded workflow reliably completes
and that its verifier detects errors. Ordinary audit logs and human corrections are
valuable without any model training.

After sufficient reviewed executions exist, consider:

- Replaying held-out cases to compare workflow/prompt versions.
- Saving useful approved examples and company rules as workflow memory.
- Supervised learning from demonstrations and corrections, if supported.
- Only then, reinforcement learning for an action-selection policy, with reliable
  outcome signals and a repeatable sandbox environment.

Memory and prompt changes are not RL. Logging corrections does not update a hosted
model's weights. Actual training requires a supported service or a policy/model we
control, appropriate data rights, and separate evaluation and promotion safeguards.

No reward function, training pipeline, or policy-promotion system is part of the
current implementation. If this stretch goal is pursued, keep correctness and
appropriate escalation ahead of speed, and keep permissions outside the learned
policy. Do not pool confidential company data across tenants without authorization.

## 8. Longer-term product boundary

- Employees contribute evidence and corrections through a small interface.
- FDEs design and operate assigned workflow automations.
- CFOs validate company financial evidence and impact.
- PE analysts see authorized portfolio results and opportunities to reuse proven work.

Use one shared evidence and canonical-data foundation. Dedicated CFO/FDE access,
workflow assignments, connectors, execution verification, and measured financial
impact are still additional implementation work. A proposed saving is not a realized
result, and a completed agent analysis is not proof that an automation was deployed.

**Implemented:** the workflow registry/approval foundation, plus recorder connection
and verified private uploads that do not require local Python analysis.
**Next:** cloud analysis and controlled report publication for those submissions.
**Deferred:** sandbox workflow execution. **Stretch:** policy improvement and RL.
Deployments and installer releases remain separate rollout steps.
