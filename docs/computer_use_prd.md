# PRD — Vista Computer-Use Executor: state-graph workflows, Jev-only decisions

Status: superseded by `docs/computer_use_system.md` (task graph v3) — kept for the run infrastructure and slice history · Owner: Yaphet · Repo: `emiliano800/vista`
Baseline: `origin/computer-use-agent` (unmerged, branches from `main@2d84379`) + `origin/jev-workflows`
Supersedes: design v1 (device runner), v2 (recorded plan as skeleton)

---

## 1. Summary

Vista records how back-office employees do work, analyzes it, and lets an FDE approve a
bounded automation. This PRD specifies the last rung: **executing approved workflows on
the employee's computer**, where

1. the *plan* is not a summary written by a model but a **state graph grown from
   recordings and verified runs** (an MDP without learning: states, actions, observed
   transitions, outcome statistics), and
2. every run-time decision is a **typed judgment by Jev over the TypeSafe System One API**
   — Jev is the policy over edges code enumerated; it never generates prose, never names a
   target, value, app or step that code did not put in front of it, and
3. **code owns every threshold, permission, limit and side effect**, and a person owns
   every irreversible commit until an edge has earned promotion.

The `computer-use-agent` branch already implements (2) and most of the run infrastructure.
This PRD adds (1), the two harness drivers that branch ships as placeholders, and the
feedback loop from runs into the graph.

## 2. Problem

- The Recording Reviewer turns a recording into a `WorkflowDefinition` (goal, inputs,
  tools, criteria). At run time the planner then decides each step *cold*: goal + up to 40
  screen controls. It has no memory of how the employee did the work, so it either asks a
  person constantly (stub/low-confidence → `none`) or generalizes with no evidence.
- A model-written plan ("summarize the recording into steps") is unauditable (who wrote
  step 4?), does not merge across recordings (variant vs. contradiction?), and rots (a
  second recording means a re-summary, not a repair).
- The recorder cannot yet observe or act: browser/desktop harnesses answer
  `harness_unsupported`.
- Product principles that must hold: observed facts ≠ recommendations ≠ realized results;
  code validates and enforces, the model interprets and selects; sandbox only; approvals
  on immutable versions; company data separated; run ≠ deployed automation.

## 3. Goals / Non-goals

**Goals**

- G1. A workflow version carries a **plan graph** compiled deterministically from ≥1
  recordings; every node/edge cites the recording events it came from.
- G2. Run-step decisions are **only** Jev judgments (`agents/jev.py` → TypeSafe
  `/systemone`); no `llm.chat` in the run loop, verifier, or promotion logic.
- G3. Each verified run and each human approve/deny becomes a **trajectory** that updates
  edge statistics; growth yields a new version that requires approval — the approved
  graph never self-modifies.
- G4. Real **browser** (Electron, isolated partition, CDP) and **desktop** (AX + native
  actuator) harnesses behind the existing `perform(step)` shape and `policy.js`.
- G5. **Sandbox only**, existing limits (`max_steps`, `max_runtime_seconds`,
  `max_cost_usd`), `submit` always paused until an edge is promoted, kill switch, lease,
  full ledger — unchanged from the branch.
- G6. Privacy line unchanged: titles, URLs, typed text, clipboard, screenshots,
  coordinates and AX paths stay on the device; only roles, control labels, slot names,
  action classes and ordering reach the cloud.

**Non-goals**

- No gradient-based RL, no learned policy weights, no reward model. "RL-style" means the
  *representation* (S, A, T, R) and the *feedback loop*, not training.
- No production (non-sandbox) execution, no real-system connectors beyond what `http`
  harness already allow-lists.
- No free-form computer-use agent (screenshot → arbitrary click). No shell, scripts, or
  model-authored tool arguments.
- No cross-company graph sharing in this PRD (portfolio-level graph merging is a follow-up
  under `firm_companies` scope).

## 4. Users and stories

| User | Story |
| --- | --- |
| Employee (recorder) | I stop a recording and see the steps Vista extracted as a graph of what I did; I can rename, drop, mark "always ask me", mark a pasted value as an input slot; I choose to share `plan.json` with the company workspace. |
| Employee (runner) | I get an offer for an approved workflow, read the notice, tick consent, watch it run in the sandbox browser/window, and can stop it with ⌘⇧Esc. Nothing in my private apps is touched. |
| FDE / tenant admin | I approve a version whose plan I can trace edge-by-edge to recordings; I decide paused steps by `step_id`; I see per-edge support and success and promote a `submit` edge to auto only when it has earned it. |
| Analyst | Realized cycle-time and exception rates per workflow, kept distinct from modeled benefit. |
| Vista operator | Every judgment, action, pause and outcome is on the ledger; Jev spend is metered per run; a change in thresholds does not re-run inference. |

## 5. Concepts

### 5.1 The MDP, in Vista terms

| RL term | Vista definition | Where |
| --- | --- | --- |
| **State** `s` | `(app_role, activity, data_signature)` — deterministic abstraction of one observation | §6.1 |
| **Action** `a` | closed primitive vocabulary × control label × slot: `type_value(control="Vendor", slot="vendor_name")` | `computer_use/harness.py` `PRIMITIVES` |
| **Transition** `T(s,a,s')` | observed edge with counts and provenance | §6.2 |
| **Reward** `R` | only non-model signals: `verify()` outcome, `effect_seen`, human approve/deny, employee correction | §6.4 |
| **Policy** `π` | Jev: `judge()` over the *outgoing edges of the current node* (+ `ask_human`, `none`) | §7 |
| **Episode** | one recording or one run; both are trajectories `s0,a0,s1,…` | §6.3 |
| **Exploration** | none by the agent; new edges only come from human trajectories (recordings, approved deviations) | §6.3 |

### 5.2 Why a graph and not a script

- Multiple recordings merge by state equality: variants → branches, rare steps → low
  support, loops → cycles. No contradiction resolution needed.
- Deviation is *defined*: current state has no outgoing edge ⇒ `ask_human`.
- Promotion is *defined*: per edge, `support ≥ S_min ∧ verified_success ≥ P_min ∧ denials = 0`.
- Provenance is *complete*: every edge lists `(recording_id | run_id, event_ids)`.

## 6. Plan graph specification

### 6.1 State abstraction (deterministic, code-only)

```text
state_key = sha1(app_role | activity | sorted(data_signature))
app_role        ∈ {accounting, crm, spreadsheet, pdf, email, browser, documents, workspace, other}
                  — from src/recorder/src/workflows.js inferEnvironment / roleOf
activity        — taskmining.abstraction.ActivityRule label, else app_role
data_signature  — set of typed tokens:
                  doc:<slot>          a declared document input is open/read
                  rec:<slot>          a declared record set is located
                  field:<control>     a control on the current screen holds a value (name only)
                  fact:<name>         a fact has been gathered (name only)
                  msg:open            a message/thread is open
                  dialog:<label>      a modal/dialog with this label is present
```

Rules:
- Built the same way from a **recording event window** (recorder, `plan.js`) and from a
  **run observation** (`compose_observation` → `state_of(observation, facts)` in
  `computer_use/graph.py`), so recorded and live states are comparable.
- Values never enter the signature; only names. Control labels pass `redact.js`; a label
  that redacts becomes its role (`field`).
- Two states are equal iff `state_key` equal. Near-equality (for `on_plan` candidates) is
  computed in code: same `app_role` and Jaccard(data_signature) ≥ 0.5.

### 6.2 Graph schema (inside `WorkflowDefinition`, hashed and approved)

```python
class GraphNode(InputModel):
    key: str                      # state_key
    app_role: AppRole
    activity: Name
    signature: list[Name] = []    # data_signature tokens
    terminal: bool = False        # a node where success criteria were verified in some trajectory

class EdgeStats(InputModel):
    support: int                  # trajectories containing this edge
    recorded: int                 # …from recordings
    executed: int                 # …from runs where the agent performed it
    verified_ok: int              # runs that ended `succeeded` after this edge
    approved: int                 # human approvals of this edge at pause
    denied: int                   # human denials
    effect_missing: int           # `effect_seen` failed after this edge

class GraphEdge(InputModel):
    id: str                       # sha1(from,key of action)
    frm: str; to: str
    action_class: Primitive       # navigate|click|type_value|press|read|extract|submit|wait|http_get|create_task
    control: Name | None = None   # UI control label
    slot: Name | None = None      # required_input or fact that fills a type_value
    produces: list[Name] = []     # facts a read/extract yields
    effect: list[Name] = []       # signature tokens expected in `to` but not in `frm`
    stats: EdgeStats
    provenance: list[Provenance]  # {source: "recording"|"run", id, event_ids|step_ids}
    policy: Literal["auto","confirm","always_ask"] = "confirm"
    anchor_ref: str | None = None # opaque id of the device-local anchor bundle

class PlanGraph(InputModel):
    start: list[str]              # entry state keys
    nodes: list[GraphNode]  (≤ 200)
    edges: list[GraphEdge]  (≤ 600)
    trajectories: int             # episodes merged
    compiled_by: Literal["recorder-plan/1"]

class WorkflowDefinition(InputModel):
    ...existing fields...
    graph: PlanGraph | None = None
```

- `definition_hash` covers `graph`; any change is a new `WorkflowVersion` → approval.
- `policy` default: `confirm` for every `WRITE_PRIMITIVES` edge, `always_ask` for
  `submit`; `read/extract/navigate/wait` default `auto`. Promotion (§6.4) may move
  `confirm → auto`; `always_ask` can be relaxed to `confirm` only by an admin edit.
- Device-local, **never uploaded**: `~/Vista/recordings/<id>/anchors.json` — per
  `anchor_ref`: AX path, role/name, bounding box, screenshot crop key, window title
  pattern. Used only to *rank* candidates on the device (§8).

### 6.3 Trajectory sources and merge

| Source | When | Who converts | Signal |
| --- | --- | --- | --- |
| Recording (stopped, reviewed by employee) | before approval | recorder `plan.js` → `plan.json` in the sharing package | `recorded += 1` |
| Run (any terminal status) | after run | worker `graph.py::merge_run()` | `executed`, `verified_ok`, `approved`, `denied`, `effect_missing` |
| Approved deviation at pause (admin lets the run take an unobserved edge; the harness observation defines `to`) | during run | same | new edge with `support=1, approved=1` |
| Employee correction during shadow mode (§9 slice 3) | run | recorder | `recorded += 1` on the corrected edge |

Merge = union of nodes by `state_key`; edges by `(frm, action_class, control, slot)`;
stats summed; provenance appended. Merged graph becomes a **draft** next version, shown
in the workspace as a diff (new edges, changed stats); the admin approves or discards.

### 6.4 Reward and promotion (all in code)

Edge-level, per version, evaluated at merge time:

```text
promote(edge) := edge.stats.executed ≥ S_min(action_class)
              ∧ verified_ok / executed ≥ P_min
              ∧ denied = 0 ∧ effect_missing / executed ≤ 0.1
S_min: read/extract/navigate 3 · click/type_value/press 5 · submit ∞ (never auto in this PRD)
P_min: 0.9
```

Proposed promotions are shown as part of the draft version diff; they take effect only
through approval. No Jev call is involved in promotion: the only model-free part of the
system is intentionally the one that widens autonomy.

## 7. Run step: Jev over the graph (TypeSafe System One)

### 7.1 API contract (existing, `agents/jev.py`)

`POST {VISTA_TYPESAFE_BASE_URL}/systemone` · `Authorization: Bearer $VISTA_TYPESAFE_API_KEY`
· body `{model: VISTA_TYPESAFE_MODEL, state, questions}` → `{answers}`; every question must
be answered or the call raises; retries on 408/429/5xx with capped backoff; modes
`stub | cassette (VISTA_JEV_CASSETTE) | live`; metered as `model_call` + one
`usage_events` row (`jev` pricing).

### 7.2 One judgment per step

State sent (code-built, redacted): goal, success criteria, input *names*, allowed tools,
remaining limits, last 5 history items (primitive, control, ok), current node
(`app_role`, `activity`, `signature`), **outgoing edges of the current node** (`id`,
templated intent, action class, control, slot, `support`, `verified_ok/executed`,
`policy`), ranked screen candidates (≤ 40), gathered fact names.

| Question | Type | Candidates (code) | Gate (code) |
| --- | --- | --- | --- |
| `node` | `choice` | current best-match node, near nodes (Jaccard ≥ 0.5, ≤ 5), `off_graph`, `none` | `p < 0.6` or `off_graph` → `deviation` → pause |
| `edge` | `choice` | outgoing edges of `node` (≤ 8, ranked by support then verified rate), `done` if node terminal, `ask_human`, `none` | `p < 0.5` → `none` semantics; two `none`s → pause (existing) |
| `target` | `pick` | screen candidates whose role/label class matches `edge.control`, then the rest; ≤ 40 | `TARGET_CONFIDENCE 0.6` (existing) |
| `value` | `pick` | only the input/fact named by `edge.slot`; if slot unset, all declared value inputs + facts | `VALUE_CONFIDENCE 0.6` (existing) |
| `irreversible` | `noul` | existing wording | `risk_threshold 0.3` (existing) |
| `effect_seen` | `noul` | asked on the *next* observation against `edge.effect` | `< 0.6` → one `wait`+re-observe, then pause `effect_missing` |

Then, deterministically, in `planner.plan_step`: edge action ∈ primitives unlocked by
`allowed_tools` ∩ harness capabilities; `policy == confirm|always_ask` or
`p_irreversible ≥ threshold` or `submit` → pause with an approval `Task`; `dry_run` stops
before any `WRITE_PRIMITIVES`; limits re-checked; `Action(step_id_for(run_id, seq))`
filed to the harness.

Verification stays exactly the branch's `verify()`: a separate `judge` over final
observation + success criteria, no history, no graph.

### 7.3 Behaviour when the graph is thin

- Node has no outgoing edges and is not terminal → `ask_human` without asking `edge`.
- `off_graph` → pause with the observation attached; the admin may approve a one-off
  primitive (existing decision flow); if approved, that becomes a new edge in the next
  draft (§6.3).
- Stub Jev (no key) → `none` → pause at step 1. Safe default, unchanged.

### 7.4 Cost

≈ 2–4k input tokens per step; output free. Edge ranking shrinks target/value candidate
lists, so cost per run should drop relative to the branch's goal-only planner. Existing
`max_cost_usd` gate applies per step from `usage_events`.

## 8. Harness drivers (recorder)

Both implement `{kind, supported, capabilities(), perform(step), close()}` and are
validated by `policy.js` before anything runs. Both return `observation.candidates` in the
branch's `Candidate` shape and use `anchors.json` only to **rank** candidates (anchor
role/name similarity first) — Jev still picks.

**Browser** — `BrowserWindow` in `partition: 'persist:computer-use'` (no cookies shared
with the employee's browser) driven via `webContents.debugger` (CDP):
`Accessibility.getFullAXTree` → candidates (`id=backendDOMNodeId`); `DOM.focus` +
`Input.insertText` for `type`; `Input.dispatchMouseEvent` at the node box for `click`;
`Page.navigate`; `Page.captureScreenshot` only if consent `screenshots: true`. No new
dependency. `navigate` is restricted to hosts listed in the version's `environment`
allow-list (sandbox tenant URLs).

**Desktop** — observe through the platform accessibility tree (macOS AX first; `files.js`
already uses it; Windows UIA later) → candidates for the focused window; act through a
small native actuator (`@nut-tree/nut-js`, pinned, ≥ 7 days published). Refuses
`isPrivateWindow`, refuses apps outside the definition's `app_role`s, pauses on any human
input seen by `uiohook` (same path as overlay Pause). Coordinates only as fallback from
*current* AX bounds, never from the recording.

**Run as recording** — capture stays on during a session (own `recordings/<id>` with
`kind: "run"`), so a run is reviewable and uploadable through the existing consent flow
and feeds `merge_run()` with `effect_seen` evidence.

## 9. Delivery slices (each shippable, each behind existing gates)

| # | Slice | Scope | Exit criteria |
| --- | --- | --- | --- |
| 1 | Land `computer-use-agent` on `main` | rebase, migration `0022`, worker handler allow-list, `VISTA_TYPESAFE_*` in infra templates | `make test`, `make test-agents`, web + recorder tests green; runs pause at step 1 without drivers |
| 2 | State abstraction + `plan.json` | `taskmining/state.py` (shared abstraction), recorder `plan.js` (events → trajectory → graph), employee review card, 4th artifact in sharing package, server accepts `graph` in `WorkflowDefinition`, Reviewer's prefilled draft includes it, hash covers it | same recording ⇒ identical `graph` hash across runs of the compiler; two recordings of `synthetic_data/front_end_work` merge into one graph with a branch |
| 3 | Planner over graph + shadow mode | `computer_use/graph.py` (`state_of`, `merge_run`), new questions in `planner.py`, cassettes, `mode: "shadow"` (observe + judge, never act; compare Jev's edge to what the employee does) | tier-1 tests with cassettes; shadow run produces per-edge agreement stats |
| 4 | Browser driver + first real run | Electron CDP harness, host allow-list, consent screenshots, run-as-recording | invoice PDF (documents harness) → sandbox web form, `submit` approved by hand, `verify()` succeeded |
| 5 | Feedback loop + promotion | `merge_run()` → draft version diff UI, per-edge stats and promotion proposals, admin approve | after N runs a `type_value` edge shows as promotable; approval makes it `auto`; `submit` stays `always_ask` |
| 6 | Desktop driver | macOS AX + nut-js, private-window refusal, human-input pause; Windows UIA follow-up | QuickBooks Desktop sandbox run end-to-end |

## 10. Acceptance criteria (product-level)

- AC1. No code path in `computer_use/` imports `llm.chat`; `ruff` rule/grep test enforces it.
- AC2. Every `Action` filed in a run has a preceding `model_call` event whose detail
  includes `edge`, `node`, and probabilities; every `model_call` has one `usage_events` row.
- AC3. A version's `graph` edges each have ≥ 1 provenance entry resolvable to a recording
  or run the tenant can open.
- AC4. Approving a version with a `graph` and then replaying its compiler on the same
  recordings yields the same `definition_hash`.
- AC5. A run whose observation state has no outgoing edge pauses within one step; a run
  in `dry_run` never files a `WRITE_PRIMITIVES` action; `submit` never executes without a
  per-step admin decision.
- AC6. Changing any threshold and re-running against cassettes issues zero new Jev calls.
- AC7. Nothing in `plan.json`, `graph`, ledger events or step requests contains a window
  title, URL path, typed value, clipboard text, coordinate, or AX path (test over fixtures
  with seeded sensitive strings).
- AC8. A run's cycle time and exception count appear in the FDE view labelled *realized*,
  separate from the modeled benefit of the workflow.

## 11. Metrics

- Coverage: % of run steps whose state matched a graph node (target ≥ 90 % after 3 recordings).
- Agreement (shadow): % of steps where Jev's chosen edge equals the employee's action.
- Autonomy: % of executed edges with `policy=auto`; number of pauses per run.
- Reliability: `verify()` success rate; `effect_missing` rate; runs failed on Jev/API errors.
- Cost: Jev USD per run vs. `max_cost_usd`; tokens per step.
- Time: realized cycle time vs. recorded baseline.

## 12. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| State abstraction too coarse (nodes collapse) or too fine (never match) | `activity` rules per app role are tunable in code; Jaccard near-match for `node`; shadow-mode agreement metric before any execution |
| Control labels leak data (e.g. a field labelled with a customer name) | `redact.js` on every label; label → role fallback; AC7 fixtures |
| Graph growth outpaces review | drafts are diffs; promotion is per edge and capped; `submit` never auto |
| Jev unavailable / degraded | retries, then job retry; run fails closed; stub mode pauses |
| Desktop actuator hits the wrong app | AX-bound targets only, app-role allow-list, private-window refusal, human-input pause, ⌘⇧Esc |
| Sandbox ≠ production UI | edges carry app_role + labels, not coordinates; deviation pauses; production execution is out of scope |

## 13. Open questions

1. Should `plan.json` sharing be a separate consent from documents sharing, or the same tick box?
2. Is `app_role` enough, or should the graph carry the app *name* for standard products (QuickBooks, Salesforce) — a privacy trade for better matching?
3. Per-tenant vs. per-workflow `ActivityRule` sets: who edits them (FDE in workspace, or code only)?
4. Should `verified_ok` require the human to confirm the realized outcome (task closed) in addition to `verify()`?
5. TypeSafe: is there a batch endpoint or streaming variant worth using for shadow-mode replays, or is one call per step fine?

## 14. Appendix — what already exists to reuse

`agents/jev.py` (`noul/choice/score/pick`, `judge`, cassette, retries) · `computer_use/planner.py`
(`plan_step`, `verify`, thresholds) · `computer_use/harness*.py` (`Observation`, `Candidate`,
`rank_candidates`, mailbox) · `computer_use/tools.py` (`REGISTRY`, `availability`) ·
`computer_use/service.py`, `handler.py` (leases, checkpoints, resume, `Ledger`) ·
`api/computer_use.py` · recorder `computer-use/{client,policy,harnesses}.js`, `sections.js`,
`workflows.js`, `redact.js`, `files.js`, `cloud.js` · `taskmining/abstraction.py`
(`ActivityRule`, `Step`), `taskmining/models.py` (`RawEvent`) · `recorder_analysis.py`
(`TOOLS`, `draft_definition`) · `jobs/queue.py` · `models/tenant.py` (`AgentRun`,
`AgentRunEvent`, `WorkflowVersion`).
