# Vista computer use — full system and task graph (v3)

This document is the current design of record for the Computer Use Agent. Part A
is the whole system end to end; Part B is the task graph in detail. It
supersedes `computer_use_whiteboard_prd.md` (v1/v2) and incorporates two
critical reviews; §B.10 lists what v3 changed from v2 and why. Every threshold in
this document is **provisional** until milestone 1 (§A.9) produces measured
distributions; none is normative before then.

---

# Part A — the full system

## A.1 One sentence

Employees' recordings are compiled on their own device into per-task graphs of
typed states; the employee and an FDE gate the graph; an approved version runs
on the employee's computer by code locating the state, Jev choosing among that
state's recorded moves when there is a choice, a harness acting through the
accessibility tree with a visible pointer, and code verifying the result by
reading it back.

Framing: **process-mined task graph + typed selection**. Not RL. Nothing learns
online; recordings add structure, runs add statistics, people approve structure.

## A.2 Components and where they live

```
employee device                                   cloud (per tenant)
┌──────────────────────────────────┐              ┌──────────────────────────────────────┐
│ Recorder (Electron)              │  plan.json   │ Recording Reviewer (worker, Jev)      │
│  ├ capture (events, AX, frames)  │ ───────────▶ │  ├ recorder_reports draft             │
│  ├ compiler  plan.js  ──▶ graph  │  labels only │  ├ findings → WorkflowDefinition draft │
│  ├ anchors.json (never leaves)   │              │  └ frame explanations (review only)   │
│  ├ plan card = checkpoint #2     │              │ Workflows API                          │
│  ├ shadow mode                   │  offers /    │  ├ versions (structural hash)          │
│  └ harnesses                     │ ◀─────────── │  ├ stats (outside hash)                │
│     ├ browser: Electron / Chrome │  results     │  └ tiers, approvals = checkpoint #1    │
│     ├ desktop: macOS AX / Linux  │              │ Computer Use Agent (worker)            │
│     │          AT-SPI2           │              │  ├ handler: lease, limits, ledger      │
│     └ region mode (research)     │              │  ├ graph planner: locate/edge/target   │
│                                  │              │  ├ Jev via TypeSafe /systemone         │
│ TypeSafe key never on device     │              │  └ verify: read-back + graded          │
└──────────────────────────────────┘              └──────────────────────────────────────┘
```

| Component | Code today | v3 role |
|---|---|---|
| Capture | `src/recorder/src/{main,intake}.js`, `preload.cjs` | events (click/type/paste/copy/key/window), AX snapshot per event, frames kept on device |
| Compiler | `src/recorder/src/plan.js`, `src/taskmining/state.py` | key-frame slicing, slot alignment, L0/L1 state, descriptors, normaliser, leakage test → `plan.json` |
| Employee gateway (#2) | recorder plan card, upload consent, Computer use tab | storyboard with one-tap exclude / always-ask; share under `computer-use-v2`; accept per run; ⌘⇧Esc |
| Recording Reviewer | `recorder_analysis.py`, `recorder_uploads.record_findings` | draft `WorkflowDefinition` with the graph, declared inputs, criteria; frame explanations for reviewers |
| Workflows API | `api/company_workflows.py`, `automation/schemas.py` | immutable structural versions; stats table; tier ladder; approval evidence |
| FDE approval (#1) | dashboard Workflows | approve a task's version from compile report + shadow report; approve tier promotions |
| Run handler | `computer_use/handler.py` | lease, `max_steps/runtime/cost`, ledger events, pauses → approval `Task`, kill switch |
| Graph planner | `computer_use/graph.py` (`planner.py` for graph-less definitions) | code locate → straight-line or Jev edge → target → effect → recovery |
| Jev | `agents/jev.py` → TypeSafe `/systemone` | typed one-of-N choices with probabilities; metered per call |
| Browser harness | `computer-use/browser.js`, `browser-electron.js`, `browser-chrome.js`, `pointer.js` | AX candidates ≤40, descriptors, real pointer + halo, fail-closed checks |
| Desktop harness | `desktop.js`, `desktop-macos.js`, `desktop-linux.js` + `desktop-linux-atspi.py` | same contract over the frontmost window; advertised only after self-test |
| Verification | `handler.py` verify step | read-back edge + code predicates; Jev only for `graded` |
| Growth | `graph_delta` in run result | stats auto-apply; structural deltas → weekly draft |

## A.3 Data flow, end to end

1. **Record.** The employee records a work session. Frames, exact control
   names, titles, URLs and coordinates stay on device (`anchors.json`).
2. **Compile (device).** `plan.js` slices the session into tasks and key frames,
   aligns slots (§B.3), computes L0/L1 state and control descriptors (§B.2,
   §B.4), runs the leakage test (§A.7) and writes `plan.json` — labels only.
3. **Employee gateway (#2).** Storyboard on device: each move shows the redacted
   thumbnail (local), the descriptor and the slot. One tap excludes a move or
   marks it always-ask. Sharing is on by default under the accepted consent
   version and can be withdrawn per recording.
4. **Review (cloud).** Recording Reviewer produces a draft `WorkflowDefinition`
   carrying the graph, declared inputs, and success criteria; a large model
   writes the task name, goal and candidate `graded` criteria from the
   descriptors (never from frames). The employee answers open questions and
   publishes.
5. **Shadow (device).** Before approval, the recorder runs the draft in shadow
   during the employee's normal work: at each located node it logs the edge and
   target it *would* take; the employee acts; code scores agreement. No
   actuation.
6. **FDE approval (#1).** The FDE approves one task from the compile report and
   the shadow report (§A.6). Approval freezes the structural hash.
7. **Run.** Tenant admin (or a schedule, later) starts a run on an approved
   version with bound inputs. The recorder pulls steps under a lease; the
   planner runs the loop in §B.6; the harness acts one edge at a time with a
   visible pointer; gates pause with an approval `Task`.
8. **Verify.** After the goal frame the harness reads the result back through a
   recorded edge; code evaluates typed criteria; Jev grades residual criteria.
   One `findings` row with the outcome, steps, cost, undo hints.
9. **Grow.** The run's `graph_delta` updates stats immediately (outside the
   hash) and queues structural deltas into a weekly draft. Tier changes follow
   §A.8.

## A.4 Model routing

| Decision | Who | When |
|---|---|---|
| Frame explanation for reviewers, task naming, goal/criteria drafting, role-level descriptor proposals from aliases | large model, offline, into drafts | compile/review |
| Node tie-break, edge at a branch, target among several matching candidates, slot when ambiguous, residual `effect_seen`, `graded` criteria | Jev | run time |
| Slicing, slot alignment, state, locate, straight-line edges, target when unique, effect diff, `read_back`, limits, gates, tiers, normaliser, leakage test | code | always |

No large-model output is an input to any run-time decision. Run-time state is
L0/L1 only (§B.2). Frame explanations are generated from redacted descriptors
and the employee's own storyboard notes — they help reviewers navigate, they do
not certify anything; certification is the compile report + shadow report.

## A.5 Bounded action contract (unchanged from the code, restated)

Closed vocabulary `navigate, click, type_value, press, read, extract, submit,
http_get, create_task, screenshot, wait, done, ask_human, none`; targets only
from the current observation's ≤40 code-enumerated candidates; values only by
slot name, resolved in code; limits per step in code and mirrored on device;
`submit` and `p_irreversible ≥ 0.3` pause unless the edge's tier allows
otherwise (§A.8); independent verification; kill switch and lease; every step
on the ledger; sandbox or opt-in real Chrome; private/sign-in/payment windows
refused.

## A.6 Human gates

**Checkpoint #2 — employee.** Consent version `computer-use-v2` accepted once
(covers descriptors, L1 skeletons, slot read-backs, shadow logging). Per
recording: share on by default, withdrawable. Per move: exclude / always-ask.
Per run: accept in the Computer use tab; kill switch.

**Checkpoint #1 — FDE.** Review unit = one task (≤25 edges; code splits larger
tasks at screen-class boundaries). The FDE sees and approves:
- *Compile report*: edges with provenance, slot table with alignment evidence
  (§B.3), leakage test result, held-out locate rate and edge coverage when ≥2
  recordings exist, alias counts.
- *Shadow report*: sessions observed, per-edge agreement, per-target agreement,
  disagreements listed with the employee's actual move (as descriptor).
  Provisional bar: edge agreement ≥ 0.9 over ≥ 3 sessions or ≥ 10 key frames,
  whichever is later. Disagreement is not automatically a defect — a legitimate
  variant becomes a structural delta the FDE can accept into the same draft.
- *Tier promotions* (§A.8): each promotion past `ask` is an explicit FDE click.

## A.7 Privacy as an enforced property

- **Allow-list grammar** for every token, descriptor field and observation
  field that leaves the device, through one normaliser: digit runs → `{n}`;
  ID patterns → `{id}`; dates → `{date}`; amounts → `{amount}`; emails/phones →
  `{email}`/`{phone}`; any token that is *not* in the app's control-vocabulary
  (the set of AX names seen on ≥2 distinct records/screens in the recording,
  i.e. names that do not vary with data) → `{text}`. Static UI words ("Save
  Changes") survive because they recur across records; data words do not.
- **Rows** are described by header names + position class, never cell text.
- **Leakage test** at compile and on every cloud-bound observation: (a) exact
  match of any recorded value, AX name outside the control-vocabulary, or
  window title fails; (b) any token not produced by the normaliser fails; (c)
  a per-app sample of on-screen OCR text (device-side) is searched for
  surviving tokens — catches PII that was visible but never typed. Known
  limits: single lower-case surnames and non-Latin scripts may pass (b) and
  are only caught by (c); this is stated in the compile report, not hidden.
- **Page text never leaves the device.** Observations sent up: L0, L1, ≤40
  descriptors, and — for `graded` criteria only — the text of the regions the
  criterion names, ≤1k, normalised, with the task's own slot values allow-listed.
- **Frames never leave the device**, including for frame explanations.

## A.8 Autonomy tiers and growth

Per-edge tier ladder, approved once per company; entry conditions computed in
code from stats, **promotion asymmetric**:

| Tier | Policy | Entry (provisional) | How it happens |
|---|---|---|---|
| shadow | propose only | new edge | automatic |
| ask | `always_ask` | shadow agreement ≥ 0.9 | automatic on approval of the task |
| confirm | `confirm` | ≥ 10 approved executions, denied = 0, effect-missing ≤ 0.05 | **FDE click** after a 7-day cooling period with the stats shown |
| unattended | `auto` | ≥ 30 verified-ok, pause rate ≤ 0.2/run, irreversibility class ≤ `navigational` **or** a covering `read_back`; harness not in region mode | **FDE click**, same cooling |

Demotion is automatic and immediate on any denial, verified failure,
`effect_missing` on a write, or leakage-test failure on an observation.
Employee `exclude`/`always_ask` and FDE overrides cap the tier. The tier is
pinned in the version at approval and changed only by these rules; stats
update freely but cannot by themselves change what the agent may do.

Structural changes (new node/edge, descriptor role, slot, criterion) go to a
weekly draft per task and need approval. Statistical changes (support,
verified-ok, denied, effect-missing, recovery-used, new alias of an existing
descriptor) apply automatically.

## A.9 Milestones and evaluation

**Milestone 0 — tooling on the synthetic CRM** (no evidence claims): leakage
test, normaliser, slot alignment, L0/L1 state, code-first locate, read-back,
recovery fragment, shadow logging, self-tests. The synthetic CRM is a fixture,
never a benchmark.

**Milestone 1 — recordings.** Acquire ≥ 3 recordings × ≥ 5 tasks × 2 real apps
from ≥ 2 people each. This is the true blocker and is not an engineering task;
it needs a design partner and the recorder in employees' hands. Output: measured
distributions for locate rate, edge coverage, pause rate, Jev calls/step,
s/step, and the **first real thresholds**, replacing every provisional number
here.

**Milestone 2 — frozen evaluation.** Per app a dev and a test set (tasks,
recordings, criteria frozen in git before any run; edits reset the number). Only
recording-compiled graphs; ≥ 1 write task per app with read-back; numbers per
commit, never combined across revisions. Report per task: held-out locate,
held-out edge coverage, code-verified success, wrong-target rate, pause rate,
recovery rate, Jev calls/step, median s/step, cost.

**Milestone 3 — shadow → ask → confirm** on the partner's tasks.

Prior result: the synthetic-CRM 6/6 (mixed revisions, hand-written graphs,
adjusted scorer) is dev smoke and is not cited.

## A.10 Harness status

| Harness | Status | Advertised when |
|---|---|---|
| Browser, Electron partition | runs live; dev smoke passed | device self-test passes (open fixture, locate 3 descriptors, click, effect) |
| Browser, employee Chrome (CDP) | runs live; dev smoke passed | same + endpoint reachable |
| Desktop macOS (AX + nut-js) | never executed | self-test on a known accessible dialog passes on that device |
| Desktop Linux (AT-SPI2 + xdotool) | unit tests + probe; one smoke failed on focus | same; focus verified by window id |
| Region mode (screenshot regions) | **research** — feasibility unknown | not before milestone 2 |

## A.11 Non-goals

Gradient RL; model-emitted coordinates/selectors/scripts; large models at run
time; self-modifying approved structure; promotions without a human click;
frames, page text, titles, URLs or values leaving the device; automating
sign-in, payment or private windows; affirmative recovery clicks.

---

# Part B — the task graph

## B.1 Structure

```
workflow = [ task, task, … ]  + non-essential frames
task     = nodes (key frames) + edges (moves) + goal node + criteria + recovery fragment (per app, shared)
node     = L0 progress state           (identity)  + L1 structure   (context)
edge     = primitive × control descriptor × slot × policy × irreversibility class × stats × provenance
```

A recording is a trajectory through nodes; a task graph is the merge of all
trajectories of that task (variants = branches, optional moves = low-support
edges). Graphs never merge across tasks. Every edge cites `(recording id, event
ids)` or `run:<id>`.

## B.2 State

Two run-time layers; both code-computed; only L0 is identity.

**L0 — progress.** The set of:
- `have:<slot>` — a task slot holds a value (typed, pasted, selected, picked).
- `read:<slot>` — a fact slot was read/extracted.
- `open:<slot>` — the record/document a slot refers to is open.
- `in:<screen-class>` — see below.
- `ctx:<dialog-class>` — a modal/dialog class is present (`autocomplete`,
  `validation`, `confirm`, `unexpected`).

Two frames are the same node iff their L0 sets are equal.

**`in:<screen-class>`** — no per-app ontology. Computed as
`hash(URL path shape ∥ sorted landmark roles ∥ heading-role count class)` where
the URL path shape replaces every segment matching `{id}`/`{n}` with its class
and where, without a URL (desktop), the window's AX landmark set and title
*shape* (normalised) are used. Under-segmentation (two different screens hash
the same) is accepted and measured as "nodes with > 6 outgoing edges"; L1
separates them when it can. Over-segmentation (the same screen hashing
differently across recordings) is the failure to watch; held-out locate rate
detects it.

**L1 — structure**, for tie-breaks, `effect_seen` and staleness only: landmark
roles present; modal present; primary button descriptor; for each control
class the *presence* of ≥1 (not counts — counts vary with data); focused
control descriptor. L1 never enters node identity.

**Frame explanation** (large model) is not a layer and is not sent at run time.

## B.3 Slot alignment — how slots exist before a workflow does

Node identity depends on slots, so slot naming is the compile step that matters
most. Algorithm, in code, on device:

1. **Transfer linkage first.** The recorder already links copy → paste and
   read → type. A value that is *read from* control A and *typed into* control
   B is one slot; its name is the normalised descriptor of A (`fact:{A}`) — the
   source, not the destination. This covers the majority of back-office
   data entry.
2. **Declared inputs second.** When a draft workflow declares inputs (from the
   Reviewer or the FDE), typed values that are not transfer-linked are aligned
   to declared inputs by *value equality across the recording* (same string
   typed into the same descriptor class in ≥1 recording) → `input:<declared>`.
3. **Cluster the rest.** Remaining typed/selected controls are clustered by
   descriptor similarity `(role, normalised name, landmark, position class)`
   across recordings; each cluster is a provisional slot named
   `field:{normalised descriptor}` — this is the one place descriptor names
   still reach identity, and it is confined to un-linked, un-declared inputs.
4. **Employee confirms once.** The storyboard shows the slot table; the employee
   can merge two provisional slots or rename one. Names are then fixed for the
   task and reused across future recordings via the cluster centroid.
5. **Evidence in the compile report**: for each slot, how it was aligned
   (transfer / declared / cluster / employee) and its recurrence across
   recordings. Slots aligned only by (3) with a single recording are flagged.

Cross-employee alignment: clusters are matched across recordings by descriptor
similarity and by their L0 position in the task (which other slots are already
held when this one is filled). Two recordings whose slot tables cannot be
aligned above threshold are kept as separate drafts and surfaced to the FDE,
not merged blindly.

## B.4 Edge

- **Primitive**: closed vocabulary (§A.5).
- **Control descriptor** (where): `(role, normalised name, landmark, position
  class, aliases[])`. Never a raw string, selector or coordinate; exact names
  and coordinates live in `anchors.json` on device and are used only as a
  first-ranked hint by the local harness.
- **Slot** (what): `input:<name>`, `fact:<name>`, `doc:<name>`, `rec:<name>`.
  Code resolves; Jev picks names only.
- **Policy**: `auto | confirm | always_ask`, from the tier (§A.8) capped by
  employee/FDE overrides.
- **Irreversibility class**, assigned in code at compile time and reviewable:
  `navigational` (navigate, click on tab/link/row/menu, read, extract, wait),
  `mutating` (type_value, press into a field, select), `committing` (submit,
  click on a descriptor whose normalised name is in the commit vocabulary —
  save/submit/send/post/delete/approve/confirm/pay — or any click that L1 shows
  closes a `ctx:confirm` dialog). Jev's `p_irreversible` may *raise* the class,
  never lower it.
- **Stats** (outside the hash): support, verified-ok, approved, denied,
  effect-missing, recovery-used, shadow-agreement.
- **Provenance**: recording/event ids or `run:<id>`.

**Typing across frames**: one edge, keyed on commit (`has_value` flip, Enter,
blur, option selected); intermediate frames non-essential; autocomplete /
search-as-you-type / validation appearing mid-typing are `ctx:` nodes with
their own edges. Values never enter the graph.

## B.5 Recovery fragment (per app, approved once)

| Interrupt (L0/L1) | Allowed recovery | Never |
|---|---|---|
| `ctx:unexpected` modal, **no textbox inside, primary button not in commit vocabulary** | `press Escape`; `click` `(button, {cancel, close, dismiss, ×})` | `ok`, `yes`, `confirm`, `continue`, or any commit-vocabulary button |
| `ctx:unexpected` modal **with** a textbox or a commit-vocabulary primary button | pause | any click |
| `in:sign-in`, session expired, payment, private | pause | any action |
| `in:<unknown screen>` | `navigate` via the app's recorded entry edge (navigational class only) | typing, clicking non-navigational controls |
| stale observation / no target above threshold | re-observe after settle, once | guessing a target |

Budget: 2 recoveries per task per run (provisional). `off_plan` pauses only
after the budget is spent. Recovery use is counted per edge and per task and
shown to the FDE; a task that survives mostly via recovery is visible as such.

## B.6 Run loop

```
observe (harness) ─▶ L0/L1 (code) ─▶ locate (code; Jev `node` only on tie)
  ─▶ edge:  one auto edge, support ≥ k, class ≤ tier allows  → straight-line (no Jev)
            else                                              → Jev `edge` over located node's edges (+ ask_human/none)
  ─▶ target: exactly one live candidate ≥ descriptor threshold → code
             several                                          → Jev `target` among them
             none                                             → recovery (§B.5)
  ─▶ gate:   class `committing`, or policy confirm/always_ask, or p_irreversible ≥ 0.3 → pause
  ─▶ act (harness; visible pointer, one edge)
  ─▶ effect: L0/L1 diff matches edge's declared effect → code; ambiguous → Jev `effect_seen`
  ─▶ goal node reached → verify (§B.7)
```

- Jev only ever sees the located node's own edges; a wrong-state pick cannot
  occur. `rejudged` remains as a counter for defects in locate, targeted at 0.
- **Straight-line is tier-bound and class-bound**: allowed only in `unattended`
  for `navigational` edges, and for `mutating` edges whose slot is a declared
  input or a transfer-linked fact; never for `committing`. In `shadow`/`ask`
  every step is judged and shown. This is the explicit trade: fast *or*
  reviewed-per-step, chosen by tier, not both at once.
- Settle: AX-mutation quiescence window 200 ms, hard cap 2 s.
- Provisional budgets to be replaced by milestone-1 data: ≤1 Jev call/step
  average in `unattended`, ≤2 in `ask`; ≤3 s/step median.
- Fail-closed rules from the code stand: stale observation, changed window,
  sensitive window, `harness_unsupported`, two consecutive `none`s, limits.

## B.7 Verification

Criteria are declared on the task as typed predicates over slots:

```
verify:
  - read_back: rec:invoice.total == input:TOTAL          # code, after read-back edge
  - read_back: rec:invoice.status in {saved, posted}     # code
  - present:   fact:confirmation_number                  # code
  - graded:    "the invoice is listed under the client"  # Jev, rubric, threshold declared here
  read_back_via: edge <id>                               # recorded edge to the record's own view
  read_back_delay: 0s | <duration>                        # for async posting
```

- **Read-back**: after the goal node the harness follows `read_back_via`,
  extracts the named slots, and code evaluates `read_back`/`present`.
  Comparison happens on device; the ledger gets slot names and booleans.
- **Delayed read-back**: apps that post asynchronously declare
  `read_back_delay`; the run stays `verifying` and a scheduled re-check runs
  the read-back edge later. Apps with no readable result at all cannot promote
  write edges past `confirm` — stated as a ceiling in the compile report, not
  worked around. A connector-based read-back (system of record API) is the
  roadmap's next rung and would lift the ceiling.
- **Graded** criteria: Jev grades from ≤1k of normalised region text the
  criterion names; the threshold is on the task, declared before any run.
- A `committing` edge with no covering `read_back` can never reach
  `unattended`.

## B.8 Generalisation

1. Identity is progress (§B.2): layout and label changes never fork the graph.
2. Descriptors with aliases (§B.4): each recording adds aliases; matching scores
   role, normalised name, landmark and position class jointly.
3. Screen-class from shape, not ontology (§B.2): no per-app schema to maintain.
4. Slot alignment by transfer linkage and L0 position (§B.3), not by label.
5. Role-level descriptors proposed offline by a large model from aliases, into
   the draft; FDE accepts.
6. No candidate above threshold → recovery → pause. Never a low-confidence click.
7. Measured by held-out locate rate and edge coverage per task (§A.9).

## B.9 Growth

- Run result carries `graph_delta`: executed / verified-ok / approved / denied /
  effect-missing / recovery-used per edge, plus any observed move not on the
  graph as a **candidate structural delta** (descriptor + slot + L0 before/after).
- Stats apply immediately to the approved version's stats table.
- Structural deltas accumulate into one draft per task per week; the FDE sees
  them alongside shadow disagreements and accepts or rejects each.
- The approved structure never changes underneath a run.

## B.10 What v3 changed from v2

| Qualm with v2 | v3 |
|---|---|
| Slots must exist before the graph does; identity moved the hard problem to slot naming | §B.3 slot alignment: transfer linkage → declared inputs → descriptor clusters → employee confirms once; alignment evidence in the compile report |
| Progress-only identity collapses screens; `in:<screen-class>` needed a per-app ontology | screen-class = hash of URL path shape + landmark roles; under-segmentation accepted and measured; L1 uses presence, not counts |
| Region mode presented as a real fallback | demoted to research; not before milestone 2; never `unattended` |
| Straight-line execution = macro replay with a fuzzy matcher, safety on one threshold | straight-line bound to tier (`unattended` only) and irreversibility class (never `committing`); trade stated explicitly |
| Recovery "click OK" can confirm a delete | recovery limited to Escape / cancel / close / dismiss; pause if modal has a textbox or a commit-vocabulary button |
| Read-back assumes the app shows what it saved | `read_back_delay` for async apps; explicit ceiling (writes stay at `confirm`) where nothing is readable; connector read-back named as the lift |
| Shadow ≥ 0.9 penalises variants and moves the gate onto employee time | disagreements become structural deltas the FDE can accept; bar is provisional and by key frames or sessions |
| Stats-outside-hash lets autonomy change without approval | promotion past `ask` requires an FDE click after a cooling period; demotion automatic; tier pinned in the version |
| Leakage test bounded by regexes; misses on-screen PII never typed | normaliser uses a per-app control-vocabulary (names recurring across records survive, data words become `{text}`); device-side OCR sample check; known limits stated |
| Frame explanation on device is either impossible or useless | explanations from redacted descriptors + employee notes, positioned as navigation for reviewers; certification is compile + shadow reports |
| Every threshold invented | all thresholds provisional until milestone 1 measures distributions |
| ~30 recordings you don't have | milestone 1 is recordings acquisition; synthetic CRM is tooling-only |
| ≤1 Jev call/step contradicts reviewed-per-step | budgets per tier; `shadow`/`ask` judge every step, `unattended` may go straight-line |

## B.11 Order of work

1. Milestone 0 on the synthetic CRM: normaliser + control-vocabulary + leakage
   test; slot alignment; L0/L1 + screen-class hash; code-first locate/effect;
   irreversibility classes; recovery fragment (restricted); read-back with
   delay; shadow logging; harness self-tests; tier pinning + FDE promotion
   click. Retire `rejudged` to a counter.
2. Milestone 1: design partner, recorder in hands, ≥30 recordings, measured
   distributions → real thresholds.
3. Milestone 2: frozen dev/test sets, per-commit numbers.
4. Milestone 3: shadow → ask → confirm on partner tasks; desktop harness only
   once its self-test row is green; region mode research in parallel, off the
   critical path.
