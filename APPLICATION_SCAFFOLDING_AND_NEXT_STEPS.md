# Vista — Application Scaffolding and Next Steps

## Start here

**Current milestone: workflow definitions, immutable versions, and approvals.**
The backend now stores a company-scoped workflow, lets an authorized reviewer decide
on a specific version, and exposes a read-only eligibility check. This is the first
small piece of the automation platform, not an automation executor.

**Next: one verified sandbox execution.** After that, connect the downloaded recorder
to the same pipeline. **Reinforcement learning (RL) is a stretch goal**, not a
requirement for this milestone or for delivering useful workflow automation.

This document describes repository implementation and planned work. It does not
certify the deployed AWS image or the installer. New APIs require the matching
backend migration and Cloudflare Worker update before they are available live.

Related documents: [current implementation](CURRENT_IMPLEMENTATION.md),
[product direction](NEXT_STEPS.md), [business context](BUSINESS_COURSE_OF_ACTION.md),
and [infrastructure](INFRASTRUCTURE.md).

## 1. The three-step milestone implemented here

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

## 5. What comes next: one verified sandbox action

The next small milestone should be:

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

## 6. Then connect the employee app to the same pipeline

The recorder should become a capture, privacy, upload, and review client; AWS should
own authoritative analysis and model calls. Employees should not need Python, a
source checkout, an AWS credential, or a model key.

The current app still needs the following work:

- Enrollment that binds an authenticated employee/device to the correct company.
  Legacy recorder `deals.id` must be mapped explicitly to canonical company scope.
- An unprocessed-session upload contract, so upload does not require local Python
  to have already produced a summary and event CSV.
- An explicit approved-artifact manifest and resumable upload state. Verify receipt
  before local cleanup; separate upload, analysis, and publication states.
- Model interpretation through the backend, with questions focused on ambiguity
  rather than requiring the employee to inspect every agent step.
- Clean-machine installer tests, packaged UI assets, and guided permissions.

Current behavior is not a guarantee that capture stays local: cloud review can
upload a report after Stop, and Submit can upload media and snapshots before removing
local files. Redaction defaults and the sharing UI need an explicit policy. A private
analysis draft should not automatically be visible to all company users.

Recording evidence and business facts remain separate: a screen observation is not
an authoritative accounting entry. Suitable documents go through mapping, validation,
and import approval before becoming canonical records.

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

**Immediate objective delivered:** create a company workflow, review an immutable
version, and record an authorized decision. **Next objective:** execute that approved
version against one sandbox tool and verify the result. **Stretch objective:** use
reviewed outcomes to improve policies, potentially including RL.
