# Recorded workflows as task graphs — PRD v2

Turn employee recordings into per-task graphs of typed states, gate them by the
employee and the FDE, and let Jev walk the approved graph on the employee's
computer. v2 revises v1 after a critical review; §0 lists what changed and why.
The long form is `computer_use_prd.md`; this is the decision-oriented version.

## 0. What v2 changes, and the qualm behind each change

| # | Qualm with v1 | v2 answer | § |
|---|---|---|---|
| 1 | The signature depends on hooked events + a usable AX tree; when either is missing there is no state, no graph. | Layered state: L0 progress tokens (primary), L1 structure, L2 regions. L2 is a real fallback computed on device, not a wish. | 2 |
| 2 | Node identity was keyed by control labels; a renamed textbox orphaned the graph downstream. | Node identity = *progress signature* over slots only. Control descriptors live on edges as aliases. | 2, 8 |
| 3 | One recording → linear chain; any deviation → pause; product dies of "it keeps asking me". | Shared per-app recovery fragments, a recovery budget before `off_plan`, and pause-rate as a gating metric. | 5 |
| 4 | Benchmark was fitted to its own failures; hand-written graphs, one app, no writes, mixed revisions. | Evaluation protocol: recorded graphs only, frozen task sets, held-out recordings, two apps, write tasks with read-back, one revision per number. 6/6 is re-labelled "dev smoke". | 10 |
| 5 | Wrong-state edge picks were patched by re-asking Jev — masking bad observations, doubling cost. | Code locates the node when the signature match is unique; Jev is asked `node` only on ties, and only ever sees the located node's edges. `rejudged` becomes a defect counter targeted at 0. | 4 |
| 6 | Frame explanation (free text from a large model) fed the run-time decision → auditability lost. | Frame explanation is review-time only. Run-time Jev sees code-computed state exclusively. | 3, 7 |
| 7 | "Labels only" was a policy, not a property: row/dialog names carry customer data; 12k page text goes to the cloud. | Redaction is a compile pass with a failing test; page text stays on device; the cloud gets descriptors + slot read-backs only. | 6 |
| 8 | FDE "reads" a graph of hashed keys → rubber stamp. Employee plan-card editing is unpaid work → nothing gets shared. | FDE approves *shadow-run evidence*, not a diagram. Employee gate is one-tap opt-out per move, share-by-default within the consent version. | 9 |
| 9 | 30–60 s for 3–4 steps; instant cursor was cosmetic. | Straight-line execution without Jev on unambiguous edges; code-only effect checks; event-driven settle. Target ≤3 s/step. | 4 |
| 10 | Desktop harness listed as a peer of browser; macOS never run, Linux failed its one smoke. | Harness is advertised only after a per-device self-test passes. Status table is honest. | 11 |
| 11 | Verification = Jev grading page text against criteria written to pass; writes never verified. | Success criteria are typed predicates over slots evaluated by code from a post-run read-back; Jev grades only residual fuzzy criteria. Unattended writes require read-back. | 5 |
| 12 | "RL-style" promises learning that never happens. | Renamed: *process-mined task graph with typed selection*. "Reward frame" → *goal frame*. | 1 |
| 13 | Every improvement costs an approval cycle. | Stats live outside the hash and update automatically; only structural changes need approval; per-edge autonomy tiers are computed from stats under a ladder approved once. | 12 |

## 1. Problem and framing

Recordings are `N×M×T` pixels plus an event list: too large to review, too
specific to replay. v1 called the fix "RL-style"; it is not — nothing learns.
What it is: **process mining** (recordings → directly-follows graph over typed
states) plus **typed selection** (Jev chooses among code-enumerated options).
Name it that so nobody expects credit assignment or exploration.

```
recording ──code──▶ key frames ──code──▶ task graph ──people──▶ approved version ──Jev+harness──▶ run
```

## 2. State: three layers, one identity

A key frame's state has three code-computed layers. Only L0 is identity.

| Layer | Content | Source | Role |
|---|---|---|---|
| **L0 progress** | which task *slots* are satisfied: `have:input:<slot>`, `have:fact:<slot>`, `open:rec:<slot>`, `open:doc:<slot>`, `in:<screen-class>`, `ctx:dialog:<class>` | recorder events + AX (§6 token grammar) | **node identity**. Deterministic, value-free, control-name-free. |
| **L1 structure** | AX skeleton: landmark roles present, count of textboxes/buttons/rows, primary-button role, modal present, form-field count | AX / DOM tree | tie-break between L0-identical nodes; `effect_seen` by code; staleness detection |
| **L2 regions** | ≤12 screen regions from a fixed grid + connected-component merge, each with a perceptual hash of its stable pixels and OCR'd *landmark* words (redacted, §6) | screenshot, on device | fallback when AX is empty or lies (Citrix, remote desktop, canvas apps, Java); region-click targets |

Rules:
- **Identity is progress, not layout.** Two frames are the same node iff their
  L0 sets are equal. A renamed textbox, a moved button, a redesigned screen do
  not create new nodes — they change edge *aliases* (§8).
- **`in:<screen-class>` is a class, not a title**: derived from the AX landmark
  set / URL path shape / L2 landmark words, normalised to a small per-app
  vocabulary at compile time (`clients-list`, `client-detail`, `new-invoice`).
- **No AX → L2 mode**, per app, decided by the recorder from tree quality
  (fraction of interactive controls with names). In L2 mode candidates are
  regions, control descriptors are `(region-class, landmark words)`, and the
  planner's `target` is a choice over region ids drawn as a numbered overlay.
  L2 mode is a declared property of the app fragment the FDE approves, with a
  lower autonomy ceiling (§12).
- **Frame explanation** (large model) is *not* a layer. It is a human-facing
  description attached to a node for review (§9) and never sent to run-time Jev.

## 3. Edge: primitive × control descriptor × slot

```
edge = (from-node, to-node, primitive, control-descriptor, slot, policy, stats, provenance)
```

- **Primitive**: the closed vocabulary (`click, type_value, press, read,
  extract, submit, navigate, wait, …`).
- **Control descriptor** (where): `(role, normalised name, landmark, position
  class, aliases[])`, never a selector, coordinate or raw string. Exact names
  and coordinates stay in `anchors.json` on device. In L2 mode: `(region class,
  landmark words)`.
- **Slot** (what): `input:<name>`, `fact:<name>`, `doc:<name>`, `rec:<name>`.
  Code resolves names to values; Jev only ever picks names.
- **Policy**: `auto | confirm | always_ask`, derived from the autonomy tier
  (§12) and employee/FDE overrides.
- **Stats** (outside the hash, §12): support, verified-ok, denied,
  effect-missing, recovery-used.
- **Provenance**: `(recording id, event ids)` or `run:<id>`.

Typing across frames is one edge: the key frame is the commit (`has_value`
flip / Enter / blur / option selected); intermediate frames are non-essential;
autocomplete/filter/validation that appear mid-typing are their own nodes
(`ctx:dialog:autocomplete`) with their own edges. Values never enter the graph.

## 4. Run loop — fewer model calls, better observations

```
observe ─▶ code: L0/L1(/L2) state ─▶ code: locate node ─▶ [unique & 1 auto edge] ─▶ act
                                        └▶ [tie / branch] ─▶ Jev: node? edge? target? ─▶ act
                              ─▶ code: effect_seen from L0/L1 diff ─▶ [ambiguous] ─▶ Jev: effect_seen
```

- **Locate in code first.** L0 equality yields a unique node most of the time;
  Jev's `node` question is asked only when ≥2 nodes tie on L0 and L1 does not
  separate them. Jev is *never* shown edges of a node other than the located
  one — the v1 "wrong-state pick" cannot occur by construction, so `rejudged`
  becomes a defect counter (target 0), not a feature.
- **Straight-line execution.** If the located node has exactly one outgoing
  edge with `policy=auto` and support ≥ `k` (default 3), execute it without
  asking Jev for `edge`. `target` is still resolved: by code when exactly one
  live candidate matches the descriptor above threshold; by Jev among the
  matching candidates otherwise. No candidate above threshold → recovery (§5).
- **Effect in code first.** `effect_seen` is the L0/L1 diff matching the edge's
  declared effect; Jev is asked only when the diff is empty or contradictory.
- **Settle is event-driven**: wait for AX-mutation quiescence (≤200 ms window,
  hard cap 2 s), not a fixed 600 ms.
- Budget: **≤1 Jev call per step on average**, ≤3 s per step median on the
  benchmark apps. Both are measured per run and reported (§10).
- Everything else from v1 stands: ≤40 candidates, targets only from the
  current observation, stale/changed/sensitive window → fail closed, kill
  switch, lease, per-step ledger.

## 5. Deviation, recovery, verification

**Recovery before pause.** Every app gets one *recovery fragment* — a small
graph approved once per app, shared by all its tasks:

| Interrupt node (L0/L1 class) | Recovery edges offered |
|---|---|
| `ctx:dialog:unexpected-modal` | `press Escape`; `click` descriptor `(button, {close,cancel,ok,dismiss}, dialog)` |
| `ctx:dialog:session-expired`, `in:sign-in` | pause (sensitive window; never automated) |
| `in:<unknown-screen>` | `navigate` to the task's entry node via the app's recorded home/entry edge |
| `stale_observation` | `wait` + re-observe (once) |
| no target above threshold | re-observe after settle (once) → then `off_plan` |

Rules: a run has a **recovery budget** (default 2 recoveries per task); recovery
edges are `auto` only if they are non-irreversible primitives on a modal; they
are counted separately in stats so a task that survives only via recovery is
visible. `off_plan` pauses happen only after the budget is spent. Pause rate is
a gating metric: a task is not eligible for `unattended` (§12) above 0.2
pauses/run over its last 20 runs.

**Verification is typed first, graded second.** Success criteria on a task are
predicates over slots:

```
verify:
  - read_back: rec:invoice.total == input:TOTAL          # code, after fresh extract
  - read_back: rec:invoice.status in {"saved","posted"}   # code
  - present:   fact:confirmation_number                   # code
  - graded:    "the invoice appears under the correct client"  # Jev, rubric, threshold declared
```

- After the goal frame, the harness performs a **read-back**: navigate to the
  record's own view (a recorded edge) and `extract` the named slots. Code
  evaluates `read_back`/`present`. Jev grades only `graded` criteria, and the
  threshold is declared on the task, not chosen after the fact.
- A **write** edge (`submit`, or any edge with `p_irreversible ≥ 0.3`) is only
  promotable past `confirm` if the task has at least one `read_back` criterion
  that covers what it wrote. No read-back → the write stays human-confirmed.
- Values used in `read_back` are compared on device where possible; only the
  boolean and the slot *name* go to the ledger.

## 6. Privacy as a property, not a policy

- **Token grammar is an allow-list**, enforced in `state.py` and `plan.js`:
  `have|open|in|ctx:<kind>:<normalised-name>`; the normaliser lower-cases,
  strips punctuation, and replaces digit runs, ID patterns (`[A-Z]{2,5}-?\d+`),
  dates, currency, emails, and any run of ≥2 capitalised words with a class
  token (`{id}`, `{date}`, `{amount}`, `{email}`, `{name}`). Table rows are
  named by **column headers + position class**, never by cell text: `row
  {client} · {id}` not `row "MER-C0010 · Valley Electric"`.
- **Test that fails on leakage**: for every recording, the exact text of every
  recorded value, every AX name and every window title is searched in
  `plan.json`; any hit fails the compile. Same test on every observation sent to
  the cloud.
- **Page text does not leave the device.** Run-time observations sent to the
  cloud contain: L0/L1 state, ≤40 control descriptors, region ids and landmark
  words (L2), and the values of *declared output slots* after redaction class
  rules — not a 12k text excerpt. Where Jev needs text for a `graded` criterion,
  the device sends only the region/control text the criterion names, capped at
  1k, through the same normaliser with an allow-list of the task's own slot
  values.
- Frame explanations (large model) are generated **on device or from redacted
  descriptors**, never from raw frames sent to the cloud, and are stored with
  the draft for reviewers only.
- Consent version bumps to `computer-use-v2` to cover L2 region descriptors and
  slot read-backs.

## 7. Model routing

| Decision | Who | When | Bound |
|---|---|---|---|
| Frame explanation, task naming, goal/criteria drafting, describing a candidate to the FDE, proposing role-level descriptors from aliases | large model | compile/review, offline | writes into a *draft*; every proposal is a suggestion a human accepts |
| Node tie-break, edge choice at branches, target among matching candidates, slot when ambiguous, residual `effect_seen`, `graded` criteria | Jev | run time | one-of-N over code-enumerated options; probabilities logged |
| Locate, straight-line edges, effect diff, `read_back` verification, limits, gates, normalisation, redaction | code | always | deterministic |

Rule unchanged and now enforced structurally: **no large-model output is an
input to a run-time decision** — the run-time state is L0/L1/L2 only.

## 8. Generalisation

1. **Identity is progress (§2)** — the largest single de-overfit: layout
   changes cannot fork the graph.
2. **Descriptors + aliases.** Run-time matching scores live candidates on
   `(role, normalised name, landmark, position class)`; each recording adds
   aliases to the same edge; support counts show what generalised.
3. **Role-level descriptors from the large model**, offline, from aliases, into
   the draft; FDE accepts or not.
4. **Threshold, not guess.** No candidate above the descriptor threshold →
   recovery → `off_plan`. Never a low-confidence click.
5. **Measured, before more planner code (§10).**

## 9. Human gates that scale

**Checkpoint #2 — employee gateway.** Share-by-default under the consent
version; the plan card shows moves as a storyboard (frame explanation + redacted
thumbnail *on device*) with one-tap *exclude* / *always ask*. Run-time: accept
per run, kill switch. Nothing leaves without the consent version accepted once
and the per-recording share not withdrawn.

**Checkpoint #1 — FDE approval approves evidence, not a diagram.**
- Review unit is one task (≤25 edges; larger tasks are split by code at
  `in:<screen-class>` boundaries).
- The FDE sees: the storyboard (frame explanations, descriptors, slots), the
  compile report (§6 leakage test, held-out locate rate, alias count per edge),
  and a **shadow-run report**: the graph was run in *shadow* on `S` real
  employee sessions (default 3) — at each key frame the planner proposed an
  edge and target, the employee acted, code recorded agreement. Approval
  requires shadow agreement ≥ 0.9 on edges and ≥ 0.9 on targets.
- Approval freezes the structural hash (nodes, edges, descriptors, slots,
  criteria, policies). Stats are outside the hash (§12).

## 10. Evaluation protocol (replaces the v1 benchmark claim)

- The 6/6 synthetic-CRM result is **dev smoke**: hand-written graphs, one
  read-only app, scorer and goals adjusted after failures, mixed revisions. It
  is not cited as success evidence.
- **Graphs come from recordings only.** No hand-written graphs in any reported
  number.
- **Frozen sets.** Per app: a *dev* task set (planner changes allowed) and a
  *test* set (tasks + recordings + criteria frozen in git before any run; edits
  reset the number). Numbers are reported per commit, never combined across
  revisions.
- **Two apps minimum**, one browser AX app and one L2-mode app; ≥5 tasks each;
  ≥3 recordings per task from ≥2 people; ≥1 write task per app with read-back.
- **Metrics per task**: held-out locate rate, held-out edge coverage,
  success (code-verified), wrong-target rate, pause rate, recovery rate, Jev
  calls/step, median s/step, cost. Thresholds are declared in the test set
  before running.
- **Order of work**: record → compile → held-out metrics **before** any further
  planner change. If held-out locate < 0.8 on either app, the state model (§2)
  is revised, not the prompts.

## 11. Harness status (honest)

| Harness | Status | Gate to advertise |
|---|---|---|
| Browser (Electron partition) | run live, dev smoke passed | per-device self-test: open fixture, locate 3 descriptors, click, effect_seen |
| Browser (employee Chrome via CDP) | run live, dev smoke passed | same + Chrome endpoint reachable |
| Desktop macOS (AX + nut-js) | **never executed** | self-test on a known accessible dialog (System Settings) must pass on the device |
| Desktop Linux (AT-SPI2 + xdotool) | unit tests + availability probe; one smoke **failed** on focus | same, on a known AT-SPI app; focus verified by window id not title |
| L2 region mode | **not built** | self-test on a canvas fixture |

A device advertises a harness only after its self-test passes and records the
result; `harness_unsupported` pauses otherwise. Desktop is not a peer of browser
until the table says so.

## 12. Growth and autonomy without an approval per improvement

- **Two kinds of change.** *Statistical* (support, verified-ok, denied,
  effect-missing, recovery-used, new aliases for an existing descriptor) apply
  to the approved version automatically — they are outside the structural hash
  and cannot change which actions are possible. *Structural* (new node, new
  edge, changed descriptor role, changed slot, changed criteria) go to a draft
  and need approval; deltas are batched into one draft per task per week.
- **Autonomy tiers per edge, computed in code**, under a ladder the FDE
  approves once per company:

| Tier | Policy | Entry condition (code, from stats) |
|---|---|---|
| shadow | propose only | default for new edges |
| ask | `always_ask` | shadow agreement ≥ 0.9 over ≥ 3 sessions |
| confirm | `confirm` | ≥ 10 approved executions, denied = 0, effect-missing ≤ 0.05 |
| unattended | `auto` | ≥ 30 verified-ok, pause rate ≤ 0.2/run on the task, write edges have read-back, harness not in L2 mode |

Demotion is automatic on any denied execution or a verified failure. The
employee's `exclude`/`always_ask` and the FDE's overrides cap the tier.

## 13. "Should we train?" — decision

No training in the loop. But the L2 layer and the shadow runs produce exactly
the labelled data a state-recognition model would need
(`(frame, L0, L1, L2, chosen edge, agreement, verified)`); store it. A trained
model may later *propose* L0 tokens for L2-mode apps offline, evaluated against
code-computed L0 on AX-capable apps first, and only into drafts behind both
checkpoints. Code-computed state stays ground truth.

## 14. Non-goals

Gradient RL; model-emitted coordinates/selectors/scripts; large models at run
time; self-modifying approved structure; raw frames, page text, titles, URLs or
values leaving the device; automating sign-in, payment or private windows.

## 15. Success criteria (v2)

- Compile: every edge cites source events; leakage test passes on every
  recording; held-out locate ≥ 0.8 and edge coverage ≥ 0.8 per task on the
  frozen test sets of both apps.
- Gates: employee can exclude/always-ask any move in one tap; FDE approval
  requires shadow agreement ≥ 0.9; hash matches what runs.
- Run: ≤1 Jev call/step average, ≤3 s/step median, pause rate ≤ 0.2/run on
  `unattended` tasks, 0 wrong targets on the test sets, every write verified by
  read-back, one visible pointer action per edge.
- Growth: stats update without approval; structural change only via draft →
  approval; tier changes are explainable from stats alone.

## 16. Order of work

1. Leakage test + normaliser + progress-identity nodes (`state.py`, `plan.js`).
2. Record 5 tasks × 3 recordings × 2 apps; compile; report held-out metrics.
3. Code-first locate/effect + straight-line execution; retire `rejudged` to a counter.
4. Recovery fragments + read-back verification.
5. Shadow mode + tier ladder + stats-outside-hash.
6. Harness self-tests; L2 region mode; desktop only once its row in §11 turns green.
