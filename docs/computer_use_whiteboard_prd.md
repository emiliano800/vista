# Recorded workflows as state graphs — simple PRD

A one-page PRD for the whiteboard design: turn employee recordings into per-task
graphs of typed states, gate them by the employee and the FDE, and let Jev walk
the approved graph on the employee's computer. The long form is
`computer_use_prd.md`; this document is the short, decision-oriented version.

## 1. Problem

Recordings are huge (`N×M×T` pixels plus an event list) and unreviewable as-is.
Automating from them today means asking a model to summarise a recording into a
plan, which nobody can audit and which cannot be composed across recordings.

## 2. Idea (left panel)

Compress each recording into a short trajectory of **key frames**, `t ≪ T`:

```
key frame_t ──action_t (Jev)──▶ key frame_{t+1} ──▶ … ──▶ reward frame
```

- **State (one key frame)** = *frame explanation* + *data signature*.
  - Frame explanation: what is on screen, in words, from the frame-explainer
    model (GPT-sol/astra) — app kind, screen/module, what the user appears to be
    doing. Interpretive; it is context for Jev, never a key the graph is indexed by.
  - Data signature: computed in code from the recorder's events — what the
    employee *holds* at that moment: `doc:<name>`, `rec:<name>`, `field:<name>`,
    `fact:<name>`, `dialog:<name>`, `msg:open`
    (`src/taskmining/state.py`, `src/recorder/src/plan.js`). Deterministic,
    labels only, no values/titles/URLs/coordinates. This is the part that makes
    two frames "the same state" across recordings and employees.
- **Action (the move between two key frames)** = primitive × control label × slot,
  the closed vocabulary (`click`, `type_value`, `press`, `read`, `extract`,
  `submit`, `navigate`, …).
  - *Control label* — **where** the action lands: accessibility role + name of the
    control (`button "Save"`, `textbox "Client name"`, `row "MER-C0010 · Valley
    Electric"`). A label, not a selector or coordinate; re-found at run time by
    matching the live accessibility tree. Coordinates/URLs stay in `anchors.json`.
  - *Slot* — **what** goes in or comes out, by *name*, never by value:
    `input:<name>` (declared workflow input), `fact:<name>` (read earlier in the
    same task), `doc:<name>` / `rec:<name>` for read/extract. Code resolves the
    name to the value at run time.
  - A "skill" is therefore one edge, e.g. `type_value · textbox "Search" ·
    input:CLIENT_NAME`.
- **Transition** = an observed edge with support counts and provenance
  (recording id, event ids).
- **Reward frame** = the key frame the recordings ended in; at run time the
  independent `verify()` decides whether the goal and success criteria hold.
- **Jev** predicts/designs the action *between* key frames: given the current key
  frame it picks the edge (and confirms the expected effect landed). It does not
  explain frames and does not invent labels or slots. Nothing is trained; the
  graph *is* the experience.

## 3. Workflow ⊃ tasks ⊃ key frames (bottom panel)

```
workflow = [ task, task, …, task ] + non-essential frames
task     = [ key frame ─edge─▶ key frame ─edge─▶ … ─edge─▶ reward frame ]
```

- A recording is sliced into frames; most are **non-essential** (scrolling,
  hesitation, unrelated windows) and are dropped or crossed out by the employee.
- The frames where the data signature changes are **key frames**; a run of key
  frames toward one reward frame is a **task**.
- Each task compiles to its own `PlanGraph`. More recordings of the same task
  merge into the same graph (variants = branches, optional steps = low-support
  edges). Graphs never merge across tasks; a workflow is an ordered list of tasks.
- Division of labour: frame explanation → GPT-sol/astra (per frame, offline, at
  compile/review time); transition between key frames within a task → Jev (at
  run time, one typed judgment per step); slicing, signatures, labels, slots,
  limits → code.

## 4. Pipeline (right panel)

```
recorder ──▶ device recordings ──▶ plan.js ──▶ checkpoint #2: employee ──▶ checkpoint #1: FDE ──▶ approved plan
                                                                                            = graph of states = workflow version
```

| Stage | Where | What is decided |
|---|---|---|
| Recorder | employee device | capture stays local; `plan.json` (graph, labels only) and `anchors.json` (titles/URLs/coords, never leaves) are compiled on device |
| **Checkpoint #2 — employee gateway** | recorder plan card + upload | exclude moves, mark moves *always ask*, tick "Share the plan"; later: accept each run in the Computer use tab, kill switch `⌘⇧Esc` |
| Recording Reviewer | cloud worker (Jev) | labels sections, asks the employee questions; employee publishes |
| **Checkpoint #1 — FDE approval** | dashboard Workflows | approve/reject version *as written*; hash freezes the graph; any edit = new version = new approval |

## 5. Run loop (dashed box)

```
key frame (screen → code-enumerated candidates + signature so far) ─▶ Jev picks next edge ─▶ harness acts ─▶ next key frame
```

- Candidates are enumerated by the harness (browser AX tree / desktop AX tree),
  capped at 40, never by the model.
- Jev answers typed questions only: `node` (which key frame am I on), `edge`
  (which observed move), `target` (which live control realises the control
  label), `value` (which slot; code resolves it), `effect_seen` (did the next key
  frame's signature appear).
- Off-graph is defined, not judged: no observed edge from the located key frame
  → `off_plan` pause; a pick from another key frame is re-asked over the located
  one's own edges; `done` only at a reward frame.
- Fail-closed: stale observation, changed window, sensitive window, no fitting
  edge → pause for a person. `confirm`/`always_ask` edges and irreversible
  primitives pause too.
- Surfaces: Electron sandbox window (default), the employee's real Chrome
  (opt-in `VISTA_CU_BROWSER=chrome`), macOS/Linux **desktop harness** for
  non-browser apps.
- Each run emits a `graph_delta` (taken / verified / denied edges). Deltas grow a
  *draft*; the approved version never changes underneath a run.

## 6. "Should we train?" — decision

Question on the board: should Jev be trained to analyse key frames and the
transitions between them from the recording itself (computer-use analysis),
instead of code computing signatures from hooked events and Jev only choosing
among observed edges?

**Decision for now: no training.** Frame explanation comes from GPT-sol/astra as
context; the data signature, key-frame slicing and edge set stay code-computed;
Jev selects.

- For training: hooked events (clicks, keys, copy/paste, window switches) miss
  state in some apps; a Jev trained on frame pairs could propose transitions the
  recorder never saw as discrete events.
- Against: model-authored key frames and edges are not auditable or
  deterministic, provenance to `(recording, event ids)` blurs, and the FDE can no
  longer approve a traceable graph.
- Door left open: every recording and run already yields
  `(frame, frame explanation, code signature, chosen edge, effect_seen, verified)`
  tuples. Store them as a labelled dataset; evaluate a trained transition model
  offline against the code graph before it may propose edges — and even then
  only into a *draft*, behind the same two checkpoints.

## 7. Non-goals

Real RL / gradient updates in the loop; free-form coordinates, selectors or
scripts from the model; self-modifying approved workflows; uploading
screenshots, titles, URLs or typed values.

## 8. Success criteria

- A recording compiles to a graph whose every edge cites its source events.
- The employee can remove or gate any move before sharing; nothing leaves the
  device without the share tick.
- The FDE approves a graph they can read; the hash matches what runs.
- Runs act one edge at a time with a visible pointer, pause at every gate, and
  end with an independent verification; synthetic CRM benchmark: 6/6 verified,
  0 wrong targets, ~$0.01 total (mixed-revision evidence, PR #7 branch).
- Growth only through `graph_delta` → new draft → approval.
