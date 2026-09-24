# Computer-use system — task graph v3 (design of record) + browser-use / macOS-use drivers

Status: design of record (v3), approved direction 2026-09-24; numbers provisional until milestone 1 · Baseline: `origin/devin/1790133120-harness-drivers` (v1 graph + hand-rolled drivers, unmerged) on top of `main@e965454`.

## 0. What exists today, and what does not

| Referenced in the v3 contract | Reality on `emiliano800/vista` |
| --- | --- |
| `docs/computer_use_system.md` (design of record) | This document. `docs/computer_use_prd.md` is the v1/v2 PRD it supersedes. |
| `taskmining/state.py`, `recorder/src/plan.js`, `computer_use/graph.py` | Exist only on the unmerged `devin/1790133120-harness-drivers` stack (4 branches, 62 files, +6.4k lines). `main` has `handler.py`, `planner.py`, `harness*.py` and the placeholder harnesses. |
| Drivers | `computer-use/browser.js` (Electron CDP + real-Chrome variant), `desktop-macos.js` (AX + nut-js, never executed), `desktop-linux.js` (AT-SPI2 + xdotool, failed its one smoke). No browser-use / macOS-use anywhere. |
| State | v1: `(app_role, activity, data_signature)` with `field:<control>` tokens; Jev asked `node`/`edge`/`target` once per step; `on_plan` by Jaccard ≥ 0.5. |

Decision needed first (§6, Q1): land the harness-drivers stack on `main` as-is and move it toward v3, or squash it into the v3 work. The plan below assumes **land it first** (it is the only code that has `PlanGraph` at all), then rewrite in place.

## 1. Where browser-use and macOS-use fit — and where they must not

Both libraries are LLM-driven agents (`Agent(task, llm)`). Vista's contract forbids exactly that: code enumerates, Jev selects, the model never names a target. So we take the **observation and actuation layers** and leave their agents, prompts and LLM clients out of the run loop.

| Layer | browser-use (Python, `browser-use` on PyPI, Playwright/CDP) | macOS-use (Python, `browser-use/macOS-use`, pyobjc AX) | Vista owner |
| --- | --- | --- | --- |
| Observe | DOM/AX service → indexed interactive-element tree with role, name, attributes, viewport flags, xpath. Feeds `Candidate` + `descriptor` + L0/L1. | AX tree walker → element tree of the focused app (role, title, value presence, actions, frame). Same. | `harness_local.py` / a new `drivers/` package |
| Act | click/type/select/scroll/press/navigate/back on an element index; new-tab, download handling. | click / type / press / open-app on an AX element. | `perform(step)` shape unchanged, `policy.js` still gates |
| Session | `BrowserSession` with own profile dir → our isolated partition; `allowed_domains` → the version's host allow-list. | one process per app, `isPrivateWindow` refusal stays ours. | recorder `computer-use/` |
| Agent / LLM / prompts | **not used.** AC1 (`no llm.chat in computer_use/`) extends to: no import of `browser_use.agent`, `browser_use.llm`, `mlx_use.agent`; enforced by a grep test. | same | — |
| Coordinates | fallback from current element box only, never from a recording | same | `anchors.json` |

Consequence: the two drivers move from the Electron/Node side to a **Python device sidecar** (`vista-device`, packaged with the recorder, spawned by `main.js`, spoken to over a local socket). That is a structural change to the recorder and the largest single cost in this plan. Reasons to accept it: (a) both libraries are Python, (b) `taskmining/state.py` already is the shared abstraction and the recorder duplicates it "byte for byte" in `plan.js` — with a sidecar the recorder can stop duplicating and call the one implementation, (c) the leakage normaliser (§4) must be one function; it can be Python only.

Pinning: both are pre-1.0 and move fast. Pin exact versions ≥ 7 days old in `pyproject.toml` (`[project.optional-dependencies] device`), record the pinned commit and the surface we depend on (`BrowserSession`, DOM tree service, AX walker) in `docs/computer_use_system.md` §drivers so upgrades are deliberate. Milestone 0 verifies the exact import paths and API names before any code depends on them — the names above are from memory of the libraries and must be checked, not assumed.

Windows: neither library covers it. It stays a stated gap (UIA later), not a silent one.

## 2. State — mapping the mind-map data types onto the v3 contract

The attached map (Task Summary · Time Tracking · App Usage · Apps Data · Files Data · Keystrokes · Mouse Movement · Keyboard Shortcuts · Copy & Paste Cache) lists what the recorder can *observe*. v3 says where each is *allowed to go*. Nothing in the map is dropped; each leaf gets exactly one home:

| Map branch → leaf | Home | Form |
| --- | --- | --- |
| **Task Summary** overview / current activity / context / outcome | not state. Review-time navigation (large-model frame explanation, `explain.js`); task `goal` + `criteria` in the approved version | text, never a run-time input |
| **Time Tracking** start/end/total; event start/end/duration; sequence timing | `EdgeStats` (outside the structural hash) + realized cycle time on the ledger | numbers; settle uses AX quiescence (200 ms / 2 s), never recorded timing |
| **App Usage** start/stop/duration/switching/active/background | `in:<screen-class>` (L0) for the active app only; switching = a `navigational` edge; durations → stats; background apps → **never captured** (privacy line) | L0 token + stats |
| **Apps Data** window titles / application content / state | L0 `in:` hash of URL-path shape + sorted landmark roles (desktop: title *shape* + landmark shape); L1 landmarks, modal, primary-button descriptor, control-class presence. Raw title and content → `anchors.json` (device only) | hashes + descriptors |
| **Apps Data** in-app actions / session data | edges (primitive × descriptor × slot); session data (cookies, auth) → never | edge |
| **Files Data** opened/created/modified/accessed/names/paths/types/history | `have:<slot>`, `open:<slot>`, `read:<slot>` (L0) with slot from alignment; file *type* → descriptor role (`document`, ext class); names/paths → `anchors.json`; history → provenance | L0 + provenance |
| **Keystrokes** individual keys / sequence / timing / text input / special keys | typing collapses to **one `type_value` edge keyed on commit**; autocomplete/validation between keystrokes → `ctx:` nodes; text input → slot (value stays on device; declared-output slot values leave redacted); special keys → `press` edge with key class; per-key timing → **dropped** from the graph (stats only) | edge + slot |
| **Mouse Movement** cursor/path/timing/clicks/scroll/drag | clicks → `click` edge on a descriptor; scroll → `navigational` `scroll` primitive (new, argument = direction class only); drag → `mutating` edge `(source descriptor, target descriptor)`; cursor position/path → **never** (coordinates stay in `anchors.json`, and only current-box fallbacks are ever used) | edge |
| **Keyboard Shortcuts** combination / timing / app-specific / system / repeated | `press` edge with canonical combo; combos in the commit vocabulary (`⌘S`, `⌘Enter`) are `committing`; system shortcuts (`⌘Tab`, `⌘Q`) are `navigational` app-switch edges; repetition → stats | edge + class |
| **Copy & Paste Cache** copied / pasted / history / times / source ctx / destination ctx | **the primary slot-alignment signal**: copy from control A, paste into B ⇒ one slot `fact:{A}` (transfer linkage); clipboard *content* never enters the graph; history → provenance; times → stats | slot |

Formally, the run-time state object the code builds and (partly) sends to Jev:

```python
class L0(InputModel):  # identity: two frames are one node iff L0 sets are equal
    have: set[SlotName]
    read: set[SlotName]
    open: set[SlotName]
    in_: ScreenClass  # sha1(url_path_shape | sorted(landmark_roles)) or title/landmark shape on desktop
    ctx: set[DialogClass]  # confirm | error | autocomplete | validation | signin | payment | unknown


class L1(InputModel):  # context: tie-breaks, effect_seen, staleness — never identity
    landmarks: list[Role]
    modal: bool
    primary_button: Descriptor | None
    control_classes: set[Role]  # presence only, never counts


class Descriptor(InputModel):
    role: Role
    name: NormalisedName
    landmark: Role | None
    position: Literal["first", "middle", "last", "only"]
    aliases: list[NormalisedName]


class Edge(InputModel):
    primitive: Primitive  # navigate|click|type_value|select|press|scroll|drag|read|extract|submit|wait
    target: Descriptor | None
    slot: SlotName | None
    policy: Tier  # shadow|ask|confirm|unattended (pinned per version)
    irreversibility: Literal["navigational", "mutating", "committing"]  # code-assigned; Jev may only raise
    stats: EdgeStats  # outside the structural hash
    provenance: list[Provenance]
```

Everything cloud-bound is L0, L1, ≤ 40 descriptors and redacted declared-output slot values; frames, page text, titles, values, paths and coordinates stay in `anchors.json`.

## 3. Work breakdown (each step is one PR, each lands behind existing gates)

Sizes are my own sessions, not people-days. Order matters: 1 → 2 → 3 are sequential; 4 and 5 can run in parallel with 3; 6–8 depend on 3+4.

| # | Step | Files | Exit criterion | Size |
| --- | --- | --- | --- | --- |
| 1 | **Design of record + land v1 stack.** Write `docs/computer_use_system.md` from the v3 contract (verbatim rules, provisional numbers flagged). Rebase `harness-drivers` stack onto `main`, merge. Add grep test: `computer_use/` and `vista_device/` import no `llm.chat`, `browser_use.agent`, `browser_use.llm`, `mlx_use.agent`. | `docs/`, `tests/test_computer_use_purity.py` | `make test` green; PRD marked superseded | 1 |
| 2 | **Normaliser + leakage test (privacy first, it gates everything cloud-bound).** One `taskmining/normalise.py`: digits/IDs/dates/amounts/emails/phones → class tokens; non-vocabulary words → `{text}`; rows named from headers + position. Control-vocabulary builder (AX names recurring across ≥ 2 records/screens). `leakage_check(payload, recording)` → fail on exact recorded value / non-vocab AX name / window title / unknown token; OCR-sample survivor search hook (device). Replaces `redact.js`'s role in identity. | `taskmining/normalise.py`, `taskmining/leakage.py`, tests with seeded sensitive fixtures | AC7 test passes on `synthetic_data/front_end_work`; known limits (lower-case surnames, non-Latin) listed in the compile report, not hidden | 1 |
| 3 | **State v3 + slot alignment + compiler.** Rewrite `taskmining/state.py`: L0/L1 per §2, `screen_class()`, `dialog_class()`, `irreversibility()` with commit vocabulary, descriptor builder, `edge_key` on `(frm, primitive, descriptor role+name, slot)`. `taskmining/slots.py`: (1) transfer linkage, (2) declared-input value equality, (3) descriptor-similarity clustering → `field:{descriptor}` flagged single-recording, (4) storyboard merges applied from `plan-edits.json`; alignment method recorded per slot. Compiler produces `task = nodes + edges + goal + criteria`, `workflow = tasks + non-essential frames`, and a **compile report** (provenance, alignment evidence, leakage result, held-out locate/coverage, aliases, under-segmentation count = nodes with > 6 out-edges). Un-alignable recordings stay separate drafts. `plan.js` shrinks to: collect events → call sidecar → render storyboard. | `taskmining/{state,slots,compile,report}.py`, `recorder/src/plan.js`, `computer_use/schemas.py` | Same recording ⇒ identical structural hash; two `front_end_work` recordings merge with one branch; stats hash separate from structural hash; compile report renders in the recorder review card | 2 |
| 4 | **Device sidecar + browser-use driver.** `src/vista_device/` (own `pyproject` extra, PyInstaller-able): local socket server; `BrowserDriver` on browser-use `BrowserSession` (isolated profile, `allowed_domains` = version allow-list, screenshots only under consent) exposing `observe() -> {candidates, descriptors, L0, L1, page_text_local}` and `perform(step)`. Settle = AX/DOM quiescence 200 ms window, 2 s cap. `main.js` spawns/health-checks it; `harnesses.js` maps `browser` to it; old `browser.js` / `browser-chrome.js` deleted after parity on the synthetic CRM. | `src/vista_device/**`, `recorder/src/computer-use/{sidecar,harnesses}.js`, `pyproject.toml` | Recorded device self-test passes on Linux + macOS; synthetic CRM 6/6 dev smoke (fixture, not benchmark); leakage check runs on every outbound observation | 2 |
| 5 | **macOS-use desktop driver.** `DesktopDriver` on macOS-use's AX layer: focused-app tree → candidates/descriptors/L0 (title-shape + landmark shape), `perform` via its click/type/press. Refuses private windows and apps outside the task's roles; pauses on `uiohook` human input. Linux AT-SPI backend kept only if its smoke passes on a recorded self-test; otherwise deleted and listed as unsupported. Windows: unsupported, stated. | `src/vista_device/desktop_macos.py`, `desktop-*.js` removed | Recorded self-test on a macOS device (child session, `platform: macos`) — the first time the desktop path ever executes | 1–2 (+ macOS VM) |
| 6 | **Run loop v3** in `computer_use/graph.py` + `planner.py` + `handler.py`. Locate by L0 equality (Jev `node` only on ties; `rejudged` counter, target 0); Jev sees only the located node's edges; `target` in code when exactly one candidate clears the descriptor threshold, Jev among several, recovery when none; `effect_seen` = L0/L1 diff first, Jev only if inconclusive; straight-line only in `unattended` for `navigational`, or `mutating` on declared/transfer slots, never `committing`; pause on `committing`, `confirm`/`always_ask`, `p_irreversible ≥ 0.3`; sign-in/session-expired/payment/private → pause always. Per-tier budgets. Recovery: one fragment per app (Escape or `(button, {cancel, close, dismiss, ×})` on a modal with no textbox and no commit-vocab primary), `in:<unknown>` → recorded entry edge only, stale/no-target → re-observe once, budget 2/task/run then `off_plan`; never ok/yes/confirm/continue. | `computer_use/{graph,planner,handler}.py`, cassettes | Tier-1 cassette tests for every rule above (each rule = one named test); AC2 ledger detail carries `node`, `edge`, `located_by ∈ {l0, jev}`, `target_by ∈ {code, jev, recovery}` | 2 |
| 7 | **Verification + tiers + growth.** Criteria as predicates over slots: `read_back` (follow `read_back_via`, optional `read_back_delay`), `present`, `graded` (Jev, ≤ 1k normalised region text, task-declared threshold). Rule: `committing` edge without covering `read_back` cannot reach `unattended`; apps with nothing readable ceiling at `confirm`. Per-edge tier `shadow → ask → confirm → unattended`; entry conditions computed from stats; promotion past `ask` = FDE click after cooling period; automatic demotion on denial / verified failure / `effect_missing` on a write / leakage failure. Structural deltas batch into a weekly draft per task; stats update every run and live outside the hash. | `computer_use/{verify,tiers,growth}.py`, `api/computer_use.py`, migration `0023` | Test: stats-only change never changes what the agent may do; demotion fires on each trigger; weekly draft diff renders | 1–2 |
| 8 | **Gates UI.** Employee: consent `computer-use-v2` once, share-by-default withdrawable per recording, one-tap exclude / always-ask per move in the storyboard, accept per run, kill switch. FDE: approve one task (≤ 25 edges) from compile report + shadow report (recorder proposed / employee acted / code-scored agreement; disagreements as acceptable structural deltas) — never hashed keys. Per `DESIGN.md`. | `recorder/ui`, `web/public/{tasks,recorder}` | Recorded walkthrough of both gates | 1–2 |
| 9 | **Evaluation harness.** Milestone 0 tooling on synthetic CRM; frozen dev/test set layout; per-commit numbers never combined across revisions; harness advertised only after a recorded device self-test. | `scripts/cu_eval.py`, `tests/fixtures/cu/` | Report template committed; M1 (≥ 3 recordings × ≥ 5 tasks × 2 real apps × ≥ 2 people) is a **data-collection blocker owned by you**, not engineering | 0.5 |

Total ≈ 12–14 sessions of engineering; external waits: a macOS machine for step 5, and real recordings for milestone 1.

## 4. Invariants to enforce in tests from step 1 (so later steps can't drift)

- Purity: no model client imports in `computer_use/`, `vista_device/`, `taskmining/`.
- Identity: `state_key` depends on L0 only; a test mutates L1 and asserts the same node.
- Names: no raw string / selector / coordinate in any `Edge`; descriptors only.
- Class monotonicity: `irreversibility(edge)` from code; `apply_jev_risk()` can raise, never lower.
- Privacy: `leakage_check` on every cloud-bound payload in tests with seeded sensitive strings; raw titles, values, paths, coordinates, clipboard text, page text never in `plan.json`, graph, ledger, or step requests.
- Straight-line: a test enumerates (tier × class × slot kind) and asserts the exact allowed cells.
- Growth asymmetry: promotion requires an FDE decision row; demotion needs none.

## 5. Risks

- browser-use / macOS-use API churn (pre-1.0). Mitigation: exact pins, thin adapter module, self-test gate.
- Sidecar packaging (PyInstaller + Electron signing on macOS). Mitigation: step 4 ships the dev path (uv) first; packaging is part of step 5's macOS session.
- Under-segmentation of `in:<screen-class>` on SPAs with stable URL shape. Accepted and measured (> 6 out-edges metric); only real recordings (M1) tell us the threshold.
- Slot alignment across people is the unknown that decides whether merged graphs are real. Un-alignable → separate drafts, by contract.

## 6. Decisions taken (2026-09-24)

1. Land `devin/1790133120-harness-drivers` on `main` first, then move it toward v3.
2. Python device sidecar: yes — browser-use / macOS-use are used as libraries (observe/act only); Jev remains the sole policy.

