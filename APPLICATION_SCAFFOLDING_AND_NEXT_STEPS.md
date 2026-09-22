# Vista — Application Scaffolding and Next Steps

## Purpose and status

This is the team's implementation guide for taking Vista from recording and
analysis to **approved workflow automation with verified outcomes and a learning
loop**.

The code-level audit behind this document used commit `ffff0ab`. Statements marked
as current describe that source baseline, not a fresh certification of the deployed
AWS image or installed desktop application. Proposed modules, tables, states, and
milestones below are design recommendations; they are not already implemented.

Related references: [current implementation](CURRENT_IMPLEMENTATION.md),
[product roadmap](NEXT_STEPS.md), [business direction](BUSINESS_COURSE_OF_ACTION.md),
and [infrastructure overview](INFRASTRUCTURE.md).

**Main conclusion:** Vista has reusable capture, storage, data-provenance, and agent
analysis infrastructure. The missing core is the control layer that turns an
observed workflow into an approved executable skill, independently verifies its
work, and preserves the outcomes needed for learning.

## 1. The intended product loop

```text
Observe authorized work and collect source records
                    |
                    v
Understand the workflow and propose an automation
                    |
                    v
Create a versioned executable workflow
                    |
                    v
Test representative cases in a sandbox
                    |
                    v
Approve that version's scope and operating limits
                    |
                    v
Agent performs the work through permitted tools
                    |
                    v
Independently verify results; collect corrections
                    |
                    v
Evaluate and improve the workflow or agent policy
                    |
                    +----> test and approve the next version
```

A report explains the work and its evidence; it is not the endpoint of the product.
A successful model response is not proof that a business action occurred.

### People and permissions

- **Employee:** contributes demonstrations and authorized files, reviews sharing,
  and answers focused clarification questions. They should not need Python, the
  repository, AWS credentials, or a model-provider key.
- **FDE (forward-deployed engineer):** designs, tests, and operates automations for
  explicitly assigned workflows and companies.
- **Portco CFO:** reviews their company's financial evidence and validates impact.
- **PE analyst:** reviews authorized portfolio results and opportunities to reuse
  proven workflows across companies.

FDE workflow access must not automatically grant portfolio financial access. CFO
visibility is a company-scoped subset of the financial platform; read, write, and
approval rights need separate definitions. Dedicated CFO/FDE roles and assignments
are not implemented at the audited baseline.

Keep observed facts, model interpretations, potential benefits, and realized
results distinct. Saved minutes alone do not establish realized financial savings.

## 2. What we already have

| Foundation | Current implementation | Reuse in the target system |
| --- | --- | --- |
| Desktop capture | Electron recording, local evidence, section review, descriptive workflow suggestions | Demonstrations, local privacy controls, employee corrections |
| Artifact storage | Recording upload API, presigned S3 uploads, document extraction | Authorized source packages and versioned evidence |
| Business data | Canonical customers, invoices, vendors, policies, purchasing and inventory, plus row provenance | Grounded workflow inputs and independent verification |
| Agent ledger | `AgentRun`, `AgentRunEvent`, `Finding`, `UsageEvent` | Traces, evidence, model usage, and execution lineage |
| Background work | Postgres jobs, worker, retry/backoff, handler registry | Central processing and workflow dispatch, after recovery improvements |
| Agent handoffs | Company-review to sector-merge barrier | A pattern for durable analysis handoffs, not a general execution engine |
| Model integration | Backend model adapter, typed phase output, response cassettes | Shared inference and repeatable model tests |
| Evaluation | Synthetic answer-key scoring and stored eval results | Analysis-quality checks; execution evaluation must be added |
| Human workflow | Tasks, findings triage, import approvals, recording corrections | Exception handling and UI building blocks |

The canonical review/sector-merge chain is real analysis orchestration. It is not
yet an executor for accounting, CRM, browser, or desktop business actions.

## 3. Important current gaps and misleading boundaries

### 3.1 Clean-install capture cannot reliably reach cloud analysis

[Recorder post-processing](src/recorder/src/main.js) looks for a repository checkout
and Python interpreter after Stop. If the checkout is absent it marks processing
as `skipped`. [RecordingUpload](src/vista/recordings.py) requires a completed summary
and event CSV, so upload depends on that local analysis succeeding.

[Packaging](src/recorder/package.json) includes recorder source and UI, but not the
Python task-mining package. The desktop UI also references shared web theme assets
outside those packaged directories. Successful installer generation is not a
clean-machine functional test.

### 3.2 Upload and privacy behavior need one explicit contract

When connected, Stop can upload a report automatically for cloud review. Submit
can upload video, screenshots, event files, and document snapshots, then remove the
local recording folder. The [media enumerator](src/recorder/src/cloud.js) walks the
recording directory by file type rather than using an explicit approved-artifact
manifest. [Recorder defaults](src/recorder/src/recorder.js) have redaction off.

Therefore claims that nothing uploads before Submit, or that screenshots and raw
capture always remain local, do not describe the current implementation. Excluding
a section from review is also not the same as removing its frames from a video.

### 3.3 A workflow suggestion is not an executable workflow

[Desktop workflow suggestions](src/recorder/src/workflows.js) describe patterns and
steps. [Config Proposer](src/vista/agents/propose.py) returns in-memory proposals.
There is no persisted workflow/version/approval lifecycle connecting these to a
production executor.

[Division Executor](src/vista/agents/execute.py) validates scopes and returns action
dictionaries. Its `status: done` and `undo` fields do not execute an external action
or implement compensation. Production handlers do not currently wire this phase to
business-system connectors; its callers are tests and the evaluation script.

### 3.4 Some workspace activity is still display scaffolding

The [portfolio service](src/vista/portfolio/service.py) `run_agent_now()` path creates
a completed `WorkspaceAgentRun`, a fixed `$0.03` cost, and a "No change since the last
run" message without dispatching a job. This is distinct from the real canonical
interpretation chain. Replace or isolate this behavior before presenting it as
workflow execution.

### 3.5 Durable storage is not complete execution recovery

The [queue](src/vista/jobs/queue.py) marks a claimed job `running`; the
[worker](src/vista/jobs/worker.py) commits the claim before invoking its handler.
Only queued jobs are subsequently claimed. There is no lease/heartbeat recovery for
a process that dies mid-run.

Some routes commit run state and enqueue work in separate transactions. A crash
between those commits can leave state without work or work without matching state.
Retries of external writes need additional safeguards beyond queue deduplication.

## 4. Missing scaffolding, by layer

### A. Company, employee, device, and workflow authorization

Current recorder uploads use `deals.id`; the portfolio uses
`platform.firm_companies.id` mapped to a tenant. Employees are not explicitly linked
to authenticated users, and devices have no enrollment model.

Add:

- A server-owned mapping between legacy deal-scoped recordings and canonical companies.
- Employee-to-user association and revocable device enrollment.
- Enrollment that resolves the employee's authorized company; an explicit picker
  if there is more than one, never a guess based on name or list position.
- Workflow assignments separating view, propose, approve, and execute permissions.
- Scope enforcement on evidence, exports, connector use, API writes, and worker
  actions, including revocation checks before consequential actions.

The existing [firm access layer](src/vista/portfolio/access.py) is reusable, but
firm membership currently exposes the firm's company list. It does not implement
assigned-workflow FDE access. Migrate old identifiers explicitly; do not recreate
or silently reassign historical company data.

### B. Cloud-first ingestion with a small desktop client

Recommended responsibility split:

| Desktop | AWS backend and worker |
| --- | --- |
| Capture with visible Start/Pause/Stop | Authorize session and company scope |
| Apply privacy policy before transmission | Accept and verify uploaded artifacts |
| Preview the sharing package | Normalize activity and extract approved documents |
| Buffer and retry uploads offline | Run model interpretation and persist results |
| Show questions and record corrections | Enforce publication and execution approvals |
| Show upload/analysis status | Meter calls and expose company/workflow results |

Add a versioned **unprocessed-session** upload contract. It must not require
`summary.json` or a local Python environment. Reuse [taskmining](src/taskmining/pipeline.py)
on the AWS worker; a bundled offline analyzer can remain a later deployment option.

Use an explicit artifact manifest with type, version, size, checksum, source, and
capture-policy version. Default to activity metadata and explicitly selected
business documents. Additional media needs a disclosed purpose and authorized
scope. Pattern redaction is not a guarantee that images or documents are safe.

Track these as separate states:

- Upload: local, uploading, accepted, failed.
- Analysis: queued, running, needs review, completed, failed.
- Publication: draft, published, withdrawn.

Uploaded-for-processing evidence should not automatically become visible to every
company member. Keep a durable local upload queue, renew expired upload URLs, and
verify required objects server-side. Only clean up local files after a verified
receipt and the applicable retention decision. Cloud processing may send approved
excerpts to a model provider; that data flow must be disclosed too.

### C. Immutable workflows and approvals

Introduce a stable `Workflow` and immutable `WorkflowVersion`. A version defines:

- Goal, supported input schema, and trigger.
- Allowed tools and company-bound connection references.
- Preconditions, business rules, and human checkpoints.
- Success criteria and verifier configuration.
- Step, runtime, and spend limits.
- Recording/file evidence supporting the design.
- Prompt/policy configuration and version identifiers.

An approval must reference the exact version, company, environment, approver, and
operating scope. Changing a tool, destination account, or permission invalidates
the previous authorization. Reviewing a finding or completing a task does not
approve arbitrary future agent actions.

The agent may choose among permitted steps; this need not be a rigid click macro.
The model does not get to redefine the permitted action set or business rules.

### D. Typed tools and a real connector boundary

Add a code-level tool registry. Each tool needs typed inputs/outputs, required
permissions, read/write classification, allowed environment, execution logic,
idempotency behavior, and a verifier. Connections bind to a company and external
account, with secrets stored by reference rather than embedded in workflow JSON.

Start with one supported accounting sandbox. Example tools:

```text
read_invoice
lookup_supplier
find_existing_invoice
create_invoice_draft
read_invoice_draft
request_human_review
```

Use APIs first. Isolated browser execution and explicitly authorized desktop
execution can be later adapters behind the same contract. The recorder's localhost
HTTP API controls recording/review operations; it is not a business-application
automation runner. Do not expose it as unrestricted remote computer access.

Only advertise compensation where a connector can really perform it. A generated
`undo_*` string is not rollback support. Treat document text and model output as
untrusted inputs, not instructions that can change tool permissions.

### E. Resumable execution and action checkpoints

Extend the existing queue/worker rather than adding another broker immediately:

- Leases, heartbeats, and recovery for expired claims.
- Atomic run/job creation where possible; durable outbox/reconciliation where needed.
- Durable per-action checkpoints and waiting-for-approval states.
- Cancellation, pause, deadline, and budget controls.
- Stable idempotency keys derived from workflow version, run, and action.
- Explicit handling of success, failure, and unknown external outcome.

If a provider creates a draft but the network response is lost, the next attempt
must reconcile using the action key or external record reference, not blindly
repeat the write. Do not promise exactly-once execution across a database and an
external API.

Keep long model/provider calls outside long-held database transactions where
possible, and preserve call receipts and usage when later stages fail.

### F. Independent verification and business outcomes

Add a verifier that reads the actual resulting state independently of the agent's
claim. Store expected values, observed values, source references, external record
ID, verifier version, and `passed/failed/inconclusive` status.

For an invoice draft, verify company, supplier, invoice number, currency, totals,
source attachment, and duplicate handling. HTTP 200 is not enough. Do not equate
model confidence with verification.

Track technical completion separately from business outcomes. Task outcome notes
and user-entered realized values are useful, but they are not automatic proof of
savings. Financial impact requires a baseline, measurement period, assumptions,
and an authorized validation decision.

### G. Learning and reinforcement-learning readiness

From the first execution, retain an episode:

```text
Input snapshot/reference
  -> observation
  -> selected action
  -> tool result
  -> independently verified next state
  -> human correction, if any
  -> final outcome
```

Record workflow/model/tool versions, attempts, approvals, latency, model cost, and
feedback lineage. Use protected references for sensitive source material; never
include credentials in the dataset.

Distinguish three mechanisms:

| Mechanism | What improves | RL? |
| --- | --- | --- |
| Workflow memory | Approved mappings, rules, useful examples | No |
| Learning from demonstrations/corrections | Behavior learned from reviewed examples | Usually supervised or imitation learning |
| Reinforcement learning | An action-selection policy optimized from outcomes and rewards | Yes |

Logging corrections does not update the currently configured hosted model's
weights. Training requires a supported training service or a model/policy we
control. A trainable decision policy around a hosted model is another option.

Add replayable sandbox cases, held-out evaluation, versioned reward calculations,
and candidate-versus-current policy comparisons. Correctness and appropriate
escalation come before speed or fewer human questions. Permissions and approval
requirements remain fixed controls outside the learned policy.

Promote improvements only after offline/sandbox evaluation and a limited rollout,
with a rollback path. Cross-company training or data pooling requires explicit
rights; reusable workflow logic does not imply permission to share customer data.

### H. One authoritative ledger and an FDE review surface

The backend currently has both `AgentRun`/`Finding` and
`WorkspaceAgentRun`/`WorkspaceFinding`. Some canonical results are mirrored; other
workspace records are display-only. Keep the execution ledger authoritative and
make workspace records explicit projections referencing it. Avoid a third
independent history for automation runs.

The first FDE screen only needs:

1. Proposed workflow with source evidence.
2. Version diff, scope, and approval.
3. Sandbox test results.
4. Run trace, verification, and exceptions.
5. Corrections and measured outcomes.

A visual workflow builder is not required for the first milestone. Employee UI
should focus on connection, recording, sharing choices, and necessary questions.

## 5. Suggested module and persistence boundaries

The following package is **proposed**, not present in the repository:

```text
src/vista/automation/
  schemas.py       Workflow and tool contracts
  service.py       Versions, approvals, assignments
  runner.py        Checkpointed execution
  tools.py         Typed tool registry
  connectors/      Sandbox adapter, then one real provider
  verification.py  Independent outcome checks
  evaluation.py    Episode replay and policy comparison
```

Minimal persistence concepts:

| Concept | Purpose |
| --- | --- |
| Workflow + version | Stable identity and immutable executable specification |
| Approval | Who authorized which version, company, scope, and environment |
| Connection | External account binding and secret reference |
| Action attempt | Durable action intent, receipt, reconciliation, and retry state |
| Verification outcome | Independently checked result and supporting evidence |
| Feedback | Human correction linked to the exact run/action/version |

Extend `AgentRun` with workflow-version, execution-mode, and input-snapshot
references rather than replacing the existing ledger. Enrollment and recording
lifecycle changes belong beside the existing auth/recordings modules. Reuse
`SourceFile` and `RecordProvenance` where possible, with explicit links to recording
artifacts instead of inventing another disconnected source inventory.

New routes must be authorized on the backend and added deliberately to the
[Cloudflare proxy](src/web/worker.mjs). API presence alone does not make an endpoint
reachable through the website.

## 6. Build order: one complete vertical slice

### Milestone 1 — approved execution, verification, and feedback

Use one supported invoice format and one sandbox accounting destination:

```text
Authorized invoice
  -> proposed workflow version
  -> FDE approval
  -> draft creation in sandbox
  -> read-back verification
  -> employee correction
  -> persisted execution episode
```

Build the queue recovery, company scope, workflow versions, approvals, connector
interface, action ledger, verifier, and feedback capture needed for this path.
A deterministic connector simulator can exercise failures and retries first; then
prove the same contract against the provider's real sandbox.

Initially a manually uploaded invoice can supply the input. This proves automation
without waiting for a broad recorder rewrite. Keep payments, posting to a real
ledger, autonomous outreach, and arbitrary desktop execution out of this slice.

### Milestone 2 — the downloaded app feeds the same pipeline

Add employee enrollment, an approved upload manifest, resumable upload, cloud task
mining, and draft/publication states. Route model features through the backend;
employees have no model keys. Fix packaged assets and test installed builds on
clean machines without a checkout or Python environment.

The recording becomes evidence for the same workflow version and run ledger, not
a second automation implementation. Do not automatically turn recorded screens or
file snapshots into authoritative financial records: suitable files pass through
the existing mapping, exception, and import-approval path.

### Milestone 3 — evaluated policy improvement

Replay reviewed cases and exceptions, compare policy/workflow versions, and measure
correctness, intervention, latency, and cost. Introduce supervised or RL optimization
where it provides measurable improvements. Data collection and versioning start in
Milestone 1; a general-purpose trainer does not need to ship before useful automation.

### Intentionally deferred

- Universal computer-use automation and broad connector coverage.
- Separate models or worker services for every employee.
- A graphical workflow builder.
- New queue infrastructure solely for architectural completeness.
- Unsupervised production exploration or automatic policy promotion.

The existing FastAPI/Postgres/ECS/S3 stack is a suitable starting point. Revisit
orchestration infrastructure only if demonstrated workflow requirements exceed it.

## 7. Acceptance tests before calling this automation

- A clean-machine installation can submit approved evidence to the correct company
  without developer tooling or provider credentials.
- An unauthorized company, user, device, or workflow assignment is denied server-side.
- Editing an approved workflow requires new approval for the changed version.
- A worker restart resumes without duplicating an external draft.
- A timeout after an external write triggers reconciliation.
- Revoking access prevents the next consequential action.
- An incorrect amount, currency, supplier, or company fails verification.
- Missing or corrupt upload objects do not produce a completed receipt or local cleanup.
- Private draft evidence is not exposed through workspace lists, exports, or downloads.
- Invalid/truncated model output cannot be treated as a successful execution.
- A correction is preserved against the exact run, action, and workflow version.
- Held-out replay tests distinguish a better policy from a worse one.
- Model usage and cost reconcile with recorded calls, including retries where usage
  is known; unknown provider outcomes are represented honestly.
- Operational improvement and modeled benefit cannot silently become realized savings.

## 8. Audit evidence and verification limits

The source review covered recorder lifecycle/upload code, authentication and company
scope, canonical imports, proposal/executor phases, worker/queue behavior, portfolio
UI service paths, models, and evaluation tests.

Eight existing proposal/executor/cassette unit tests passed with model calls disabled.
These tests validate the current in-memory phase behavior; they do not prove an
external action, clean-machine installation, or production execution pipeline.
No live model calls, production jobs, or deployments were performed for this audit.

Useful implementation starting points:

- [Recording lifecycle and local analysis](src/recorder/src/main.js)
- [Upload package and media handling](src/recorder/src/cloud.js)
- [Recording API and review queueing](src/vista/api/recordings.py)
- [Canonical import and approval flow](src/vista/portfolio/imports.py)
- [Company-review / sector-merge handoff](src/vista/portfolio/interpret.py)
- [Proposal phase](src/vista/agents/propose.py) and [executor scaffold](src/vista/agents/execute.py)
- [Job queue](src/vista/jobs/queue.py) and [worker](src/vista/jobs/worker.py)
- [Tenant models](src/vista/models/tenant.py) and [platform models](src/vista/models/platform.py)
- [Agent evaluation](src/vista/agents/eval.py) and [phase tests](tests/test_agents.py)

**First implementation goal:** one scoped, approved workflow that performs a real
sandbox action, independently verifies it, and records feedback. Connect the recorder
to that path and use its outcomes as the foundation for learning.
