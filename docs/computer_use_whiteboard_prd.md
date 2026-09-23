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

Compress each recording into a short trajectory of **typed states**, `t ≪ T`:

```
state_t ──action_t (Jev)──▶ state_{t+1} ──▶ … ──▶ reward state
```

- **State** = `(app_role, activity, data_signature)`. The signature is what the
  employee *holds* at that moment: `doc:<name>`, `rec:<name>`, `field:<name>`,
  `fact:<name>`, `dialog:<name>`, `msg:open`. Computed in code
  (`src/taskmining/state.py`, `src/recorder/src/plan.js`) — deterministic, labels
  only, no values/titles/URLs/coordinates.
- **Action** = closed primitive vocabulary (`click`, `type_value`, `press`, `read`,
  `extract`, `submit`, `navigate`, …) × control label × slot.
- **Transition** = an observed edge with support counts and provenance
  (recording id, event ids).
- **Reward state** = the terminal state the recording ended in; at run time the
  independent `verify()` decides whether the goal and success criteria hold.
- **Policy** = Jev, choosing among the observed outgoing edges of the located
  state. Nothing is trained; the graph *is* the experience.

## 3. One task, one graph (bottom panel)

A recording is sliced into moves; the employee may cross moves out. The kept
moves become a `PlanGraph` for **that task only**. More recordings of the same
task merge into the same graph (variants = branches, optional steps = low-support
edges). Graphs never merge across tasks.

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
state (screen → code-enumerated candidates) ─▶ Jev decides next edge ─▶ harness acts ─▶ next state
```

- Candidates are enumerated by the harness (browser AX tree / desktop AX tree),
  capped at 40, never by the model.
- Jev answers typed questions only: `node` (where am I), `edge` (which observed
  move), `target`, `value` (declared inputs resolve in code), `effect_seen`.
- Fail-closed: stale observation, changed window, sensitive window, no fitting
  edge → pause for a person. `confirm`/`always_ask` edges and irreversible
  primitives pause too.
- Surfaces: Electron sandbox window (default), the employee's real Chrome
  (opt-in `VISTA_CU_BROWSER=chrome`), macOS/Linux **desktop harness** for
  non-browser apps.
- Each run emits a `graph_delta` (taken / verified / denied edges). Deltas grow a
  *draft*; the approved version never changes underneath a run.

## 6. "Should we train?" — decision

Question on the board: should Jev be trained to recognise states and
transitions directly from the recording (computer-use analysis), instead of code
computing them from hooked events?

**Decision for now: no.** Keep states code-computed.

- For: code states only see what the recorder hooks (clicks, keys, copy/paste,
  window switches); a trained recogniser could see states in apps the hooks miss.
- Against: model-authored states are not auditable or deterministic, edge
  provenance blurs, and the FDE can no longer approve a traceable graph.
- Path that keeps the door open: store `(observation, code_state)` pairs from
  recordings and runs as a labelled dataset; evaluate any future recogniser
  offline against code states before it is allowed to influence the graph.

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
