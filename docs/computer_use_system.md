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

## 2b. Recorder data types — mind map vs. what is recorded today

Legend: **yes** = written today (`events.jsonl` / `manifest.json` / `files.json` / `shots/`); **partial** = derivable or only under a setting; **no** = not captured. "v3" = how the task-graph v3 contract treats it (L0 = node identity, L1 = context, non-essential = deliberately not state). "Leaves device" = included in the metadata package uploaded by `intake.js` (only `timestamp, event_type, app, count` per event, plus opt-in shared documents and the compiled plan).

| Branch | Leaf | Recorded on device | Where | v3 treatment | Leaves device |
| --- | --- | --- | --- | --- | --- |
| Task Summary | Task overview | partial — employee's intent text at Start (`summary_text`) | manifest | task `goal` (large model drafts, human approves) | yes (as recording name / plan goal) |
| | Current activity | partial — post-session review labels per section (AI explanation, employee approve/fix) | review.json | `activity` is v1 node key; v3 drops it from identity | no (labels only if plan shared) |
| | Task context | partial — review `unclear` / `questions` | review.json | review-time only | no |
| | Task outcome | **no** (nothing marks success at record time) | — | terminal *goal frame* + `criteria`; verified at run time by read-back | — |
| Time Tracking | Start / end time | yes | manifest | session bounds | yes |
| | Total duration | yes (`active_seconds`, pauses cut out) | manifest | — | yes |
| | Event start | yes — every event has `timestamp` | events.jsonl | frame ordering | yes (bucketed timestamps) |
| | Event end / duration | partial — derived from the gap to the next event; keys are coalesced | events.jsonl | non-essential | no |
| | Sequence timing | partial — derived (stretches, switch gaps, transfer latency computed server-side) | server `observe()` | edge `stats` (latency), outside the hash | yes (derived) |
| App Usage | Application start / stop | **no** — only focus changes; a process launching or quitting is not seen | — | not state | — |
| | Usage duration | yes (`appSeconds` per app, `apps` summary) | manifest | — | yes (derived) |
| | Application switching | yes (`focus` event on every foreground change, `window_id`) | events.jsonl | `in:<screen-class>` change | yes |
| | Active app | yes (`app` on every event; this was the Meridian bug, fixed in 1ccb725) | events.jsonl | `app_role` / screen class | yes |
| | Background app | **no** — only the frontmost window is polled | — | not state | — |
| Apps Data | Application content | partial — screenshots on focus/change/periodic (`screen` events, JPEGs); no accessibility tree is recorded today | shots/ | L1 landmarks + control-class presence (needs AX capture — **not implemented**) | no |
| | Window titles | yes (redacted; `(private)` for private apps) | events.jsonl | `in:<screen-class>` shape on desktop | no |
| | In-app actions | yes — click / key / shortcut / scroll / copy / paste attributed to app + title | events.jsonl | edges (primitive × control × slot) | counts only |
| | Session data | **no** (no login/session identity captured; sign-in windows are muted) | — | fail-closed | — |
| | Application state | partial — screenshots only; no `has_value`, modal, or field state | shots/ | L1 (modal present, primary button, presence) — **not implemented** | no |
| Files Data | Opened files | yes — `FileTracker` probes the frontmost app every 5 s (AX document / lsof / window title) | files.json | `open:<slot>` (L0) | names only, content opt-in |
| | Created files | partial — `first_seen` inside the session, and `edited` (mtime ≥ session start) when a snapshot is shared | files.json | effect / read-back | opt-in |
| | Modified files | partial — `modified_at`, `edited` on shared snapshots only | files.json | effect / read-back | opt-in |
| | Accessed files | yes — open intervals + point uses | files.json | `open:` / `read:` | opt-in |
| | File names / paths | yes (path locally; `safeName` when shared) | files.json | slot name | name only |
| | File types | yes (`ext`, `content_type`) | files.json | doc kind | yes |
| | Interaction history | yes — per-file intervals with app and seconds; not per-click | files.json | edge stats | opt-in |
| Keystrokes | Individual keys | yes — non-printable key names always; printable chars only with `keyContent` on and never in sign-in/private windows | events.jsonl | non-essential | count only |
| | Key sequence | yes (event order) | events.jsonl | one `type_value` edge per field | count |
| | Key timing | yes (per-event timestamps) | events.jsonl | non-essential | no |
| | Text input | partial — `keyContent` setting, redacted, masked in sensitive windows | events.jsonl | value never in graph; slot only (`have:<slot>`) | never |
| | Special keys | yes (`key` name in payload) | events.jsonl | `press` primitive | count |
| Mouse Movement | Cursor position | partial — only at click time (`x, y`) | events.jsonl | anchors.json only (never in graph) | no |
| | Movement path / timing | **no** (no mousemove hook) | — | non-essential | — |
| | Clicks | yes (button, x, y, click count) | events.jsonl | `click` edge on a control descriptor (needs AX at click — **not implemented**) | count |
| | Scroll activity | yes (direction, amount) | events.jsonl | non-essential | count |
| | Drag actions | **no** | — | `drag:<from>→<to>` proposed in PRD §7; no primitive yet | — |
| Keyboard Shortcuts | Shortcut combination | yes (`shortcut` event, modifiers + key) | events.jsonl | `press` / `key:<combo>` | count |
| | Shortcut timing | yes (timestamp) | events.jsonl | non-essential | no |
| | Application-specific vs system | partial — app is on the event; no classification | events.jsonl | — | — |
| | Repeated shortcut use | partial — derivable from counts | events.jsonl | edge `stats.support` | count |
| Copy & Paste Cache | Copied / pasted content | partial — always a salted 12-char hash + length; text only with `clipboard` setting, redacted | events.jsonl | slot linkage (`fact:{source}` — the v3 slot-alignment rule 1) | never (hash stays local too) |
| | Clipboard history | partial — only the *last* copy is held for linking | recorder memory | one transfer per paste | — |
| | Copy / paste time | yes | events.jsonl | transfer latency stat | yes (derived) |
| | Source context | yes on linked pastes (`source_app`, `source_title`, `transfer_ms`, `cross_app`) | events.jsonl | `fact:{A}` slot | app pair only |
| | Destination context | yes (paste event's own app/title) | events.jsonl | `field:{B}` control | app only |

### Decision (2026-09-24): capture everything on device; upload only the normalised graph

The two columns are governed separately. **On device, every leaf in the mind map is captured in full** — typed text, clipboard text and history, URLs, window titles, screenshots and video, raw event logs, app start/stop, background apps, mouse path, drags, file create/modify — because review and compile happen on the device and the richer the local recording, the better the storyboard, the slot alignment and the goal frame. **What leaves the device does not change**: the normalised graph (L0, L1, ≤ 40 descriptors, redacted declared-output slot values), plus the counts/timestamps/app names the metadata package already carries — and the leakage gate stays on every upload. Every `no` / `partial` in the "Recorded on device" column is therefore a gap; step 2c below closes them. Nothing in the "Leaves device" column moves.

### Gaps that matter for v3, and which step closes each

1. **No accessibility-tree capture during recording.** L1 landmarks, control descriptors on clicks, `has_value` and the screen's landmark shape all need the AX tree at each key frame. Today the recorder has coordinates + screenshots + window title; the AX walk exists only in the computer-use drivers. This is the single biggest missing data type and is why the sidecar (step 4) is pulled ahead of state v3: the recorder captures frames through the *same* sidecar `observe()` the run loop uses, so recording and execution share one state function by construction. Until it lands, v3 nodes can only be compiled from URL/title shape and `files.json`, which is enough for the L0 `open:`/`in:` sets but not for descriptors.
2. **App start/stop and background apps** are not observed. Captured in step 2c (process list poll on the same 5 s tick as `FileTracker`: `app_start` / `app_stop` events, `background_apps` on each `focus` event). They remain non-essential frames in v3 — recorded, shown in the storyboard timeline, never node identity.
3. **Drag** has no hook; **mouse path** is not recorded. Step 2c adds a `mousemove` sample stream (throttled, local only) and a `drag` event (down → move → up across controls). Drag becomes a `mutating` primitive `drag(from_descriptor, to_descriptor)` in step 3; the path stays non-essential.
4. **Created vs modified files** are only distinguishable when a snapshot is shared. Step 2c records `created` / `modified` for every file the frontmost app touches (mtime and birth-time at first sight, snapshot at session end), always locally; v3 still uses a read-back effect signal at run time (step 7), and file mtimes never leave the device.
5. **Task outcome** is never recorded. Step 2c adds an explicit "done" marker the employee presses at the end of a task (frame + timestamp); step 3 turns it into the goal frame + criteria approved in the storyboard.
6. **Clipboard history** is one entry deep. Step 2c keeps the full local clipboard history for the session (text, hash, length, source app/title, timestamp); step 3's slot-alignment rule 1 links a paste to the matching copy by hash across that history, so copy A, copy B, paste B, paste A resolves correctly. Only the hash-derived slot linkage reaches the graph.
7. **Text input, URLs, titles, screenshots** are captured only under settings (`keyContent`, `clipboard`) or redacted. Step 2c makes full capture the default for recording (settings become per-recording *exclusions*, still one-tap), keeps the sign-in / private-window mute, and adds periodic screen video alongside the screenshots. All of it stays in the local recording directory and is what the storyboard and compiler read.
8. **Everything that leaves the device** (counts, bucketed timestamps, app names, plan goal, recording name) goes through the step-2 normaliser and leakage check as of step 2b, so the "Leaves device" column becomes an enforced property rather than a description.

## 3. Work breakdown (each step is one PR, each lands behind existing gates)

Sizes are my own sessions, not people-days. Status as of 2026-09-24: steps 1 and 2 are on `main` (#8, #9, #10, #11 — #11 pulled the dashboard half of step 7 forward); 2b is #13 and 2c follows it (`recording_format: 2` — `path`/`drag`/`app_start`/`app_stop`/`done` events, 32-entry clipboard history with `history_depth`, raw local titles/text by default, `apps_seen`, `outcome`, per-file `created`/`modified`; the process list comes from `get-windows` `openWindows()` on every platform and the file probe's `running` on macOS). Remaining order: 2b → 2c → 4 → 3 (3 needs the sidecar's AX frames, per §2b gap 1, and the full local recording from 2c); 5 in parallel with 3; 6 → 7 → 8; 9 alongside.

| # | Step | Files | Exit criterion | Size |
| --- | --- | --- | --- | --- |
| 1 | **Design of record + land v1 stack.** Write `docs/computer_use_system.md` from the v3 contract (verbatim rules, provisional numbers flagged). Rebase `harness-drivers` stack onto `main`, merge. Add grep test: `computer_use/` and `vista_device/` import no `llm.chat`, `browser_use.agent`, `browser_use.llm`, `mlx_use.agent`. | `docs/`, `tests/test_computer_use_purity.py` | `make test` green; PRD marked superseded | 1 |
| 2 | **Normaliser + leakage test (privacy first, it gates everything cloud-bound).** One `taskmining/normalise.py`: digits/IDs/dates/amounts/emails/phones → class tokens; non-vocabulary words → `{text}`; rows named from headers + position. Control-vocabulary builder (AX names recurring across ≥ 2 records/screens). `leakage_check(payload, recording)` → fail on exact recorded value / non-vocab AX name / window title / unknown token; OCR-sample survivor search hook (device). Replaces `redact.js`'s role in identity. | `taskmining/normalise.py`, `taskmining/leakage.py`, tests with seeded sensitive fixtures | AC7 test passes on `synthetic_data/front_end_work`; known limits (lower-case surnames, non-Latin) listed in the compile report, not hidden | 1 |
| 2b | **Normaliser everywhere cloud-bound.** Route every producer through `taskmining.normalise` + `leakage.check`: `intake.js` metadata package (recording name, plan goal, app names), recorder compiler output, run observations, ledger rows, compile report. Leakage check becomes a CI gate with the seeded fixtures. Consent version bumps to `computer-use-v2`; a recording under an older consent never uploads. | `recorder/src/{intake,plan}.js`, `computer_use/{handler,graph}.py`, `taskmining/compile.py`, consent migration | Every row in the §2b "Leaves device" column is covered by a test that seeds the recorded value and asserts the payload fails the check if it survives | 1 |
| 2c | **Capture completeness (device only).** Close every `no` / `partial` in §2b's "Recorded on device" column: `app_start` / `app_stop` + `background_apps` (process poll), `mousemove` samples + `drag` events (uiohook), full clipboard history (text + hash + source), text input / URLs / titles unredacted in the local log (redaction moves to the upload boundary; sign-in and private windows stay muted at capture), periodic screen video beside screenshots, per-file `created` / `modified`, an explicit task-done marker. Settings become per-recording exclusions. Recording directory layout versioned (`recording_format: 2`). The upload package is unchanged and the leakage gate from 2b runs on it. | `recorder/src/{capture,files,clipboard,intake}.js`, `recorder/ui/dashboard.html` | Every leaf in the mind map appears in a local recording of the synthetic CRM fixture; `intake.js` upload of that recording passes `leakage.check` with every seeded value present locally | 1–2 |
| 3 | **State v3 + slot alignment + compiler.** Rewrite `taskmining/state.py`: L0/L1 per §2, `screen_class()`, `dialog_class()`, `irreversibility()` with commit vocabulary, descriptor builder, `edge_key` on `(frm, primitive, descriptor role+name, slot)`. `taskmining/slots.py`: (1) transfer linkage, (2) declared-input value equality, (3) descriptor-similarity clustering → `field:{descriptor}` flagged single-recording, (4) storyboard merges applied from `plan-edits.json`; alignment method recorded per slot. Compiler produces `task = nodes + edges + goal + criteria`, `workflow = tasks + non-essential frames`, and a **compile report** (provenance, alignment evidence, leakage result, held-out locate/coverage, aliases, under-segmentation count = nodes with > 6 out-edges). Un-alignable recordings stay separate drafts. `plan.js` shrinks to: collect events → call sidecar → render storyboard. Consumes 2c's full local recording: clipboard-history hash linkage for slot rule 1, `drag` primitive, task-done marker → goal frame + criteria drafted in the storyboard, app start/stop and mouse path shown as non-essential frames. | `taskmining/{state,slots,compile,report}.py`, `recorder/src/plan.js`, `computer_use/schemas.py` | Same recording ⇒ identical structural hash; two `front_end_work` recordings merge with one branch; stats hash separate from structural hash; compile report renders in the recorder review card | 2 |
| 4 | **Device sidecar + browser-use driver — used by the recorder too (§2b gap 1).** `src/vista_device/` (own `pyproject` extra, PyInstaller-able): local socket server; the recorder calls `observe()` on every focus / click / commit frame and stores `L0`, `L1`, the descriptor under the pointer and `has_value` per control beside the screenshot in `events.jsonl`, so recorded frames and run-time frames come from one state function; `BrowserDriver` on browser-use `BrowserSession` (isolated profile, `allowed_domains` = version allow-list, screenshots only under consent) exposing `observe() -> {candidates, descriptors, L0, L1, page_text_local}` and `perform(step)`. Settle = AX/DOM quiescence 200 ms window, 2 s cap. `main.js` spawns/health-checks it; `harnesses.js` maps `browser` to it; old `browser.js` / `browser-chrome.js` deleted after parity on the synthetic CRM. | `src/vista_device/**`, `recorder/src/computer-use/{sidecar,harnesses}.js`, `pyproject.toml` | Recorded device self-test passes on Linux + macOS; synthetic CRM 6/6 dev smoke (fixture, not benchmark); leakage check runs on every outbound observation | 2 |
| 5 | **macOS-use desktop driver.** `DesktopDriver` on macOS-use's AX layer: front-app tree → candidates/descriptors/L0 (title-shape + landmark shape), `perform` via its `AXPress`/value-set/scroll, key presses from a closed key table (Quartz), screenshots to a device-local file under consent, `extract` from AX values (device only). Bound to the recorder's front pid or an `app` it launches. Advertised only when `health.self_tests.desktop` is true: `python -m vista_device selftest desktop` writes `~/.vista/device/selftest-desktop.json` (this sidecar version, this platform, L0 non-empty, settled, leakage ok); the recorder's own probe applies the same rule. Linux AT-SPI and Windows: unsupported, stated. **Status: implemented against the pinned macOS-use API; not yet executed on a macOS device — the self-test is the gate.** | `src/vista_device/drivers/desktop_macos.py`, `src/vista_device/selftest.py` | Recorded self-test on a macOS device — the first time the desktop path ever executes | done (code) / pending (device) |
| 6 | **Run loop v3** in `computer_use/graph.py` + `planner.py` + `handler.py`. Locate by L0 equality (Jev `node` only on ties; `rejudged` counter, target 0); Jev sees only the located node's edges; `target` in code when exactly one candidate clears the descriptor threshold, Jev among several, recovery when none; `effect_seen` = L0/L1 diff first, Jev only if inconclusive; straight-line only in `unattended` for `navigational`, or `mutating` on declared/transfer slots, never `committing`; pause on `committing`, `confirm`/`always_ask`, `p_irreversible ≥ 0.3`; sign-in/session-expired/payment/private → pause always. Per-tier budgets. Recovery: one fragment per app (Escape or `(button, {cancel, close, dismiss, ×})` on a modal with no textbox and no commit-vocab primary), `in:<unknown>` → recorded entry edge only, stale/no-target → re-observe once, budget 2/task/run then `off_plan`; never ok/yes/confirm/continue. | `computer_use/{graph,planner,handler}.py`, cassettes | Tier-1 cassette tests for every rule above (each rule = one named test); AC2 ledger detail carries `node`, `edge`, `located_by ∈ {l0, jev}`, `target_by ∈ {code, jev, recovery}` | 2 |
| 7 | **Verification + tiers + growth.** (Dashboard half landed in #11: graph view, run paths, drafts folded from runs, admin-selected promotion, automatic demotion.) Remaining: Criteria as predicates over slots: `read_back` (follow `read_back_via`, optional `read_back_delay`), `present`, `graded` (Jev, ≤ 1k normalised region text, task-declared threshold). Rule: `committing` edge without covering `read_back` cannot reach `unattended`; apps with nothing readable ceiling at `confirm`. Per-edge tier `shadow → ask → confirm → unattended`; entry conditions computed from stats; promotion past `ask` = FDE click after cooling period; automatic demotion on denial / verified failure / `effect_missing` on a write / leakage failure. Structural deltas batch into a weekly draft per task; stats update every run and live outside the hash. | `computer_use/{verify,tiers,growth}.py`, `api/computer_use.py`, migration `0023` | Test: stats-only change never changes what the agent may do; demotion fires on each trigger; weekly draft diff renders | 1–2 |
| 8 | **Gates UI.** Employee: consent `computer-use-v2` once, share-by-default withdrawable per recording, one-tap exclude / always-ask per move in the storyboard, accept per run, kill switch. FDE: approve one task (≤ 25 edges) from compile report + shadow report (recorder proposed / employee acted / code-scored agreement; disagreements as acceptable structural deltas) — never hashed keys. Per `DESIGN.md`. | `recorder/ui`, `web/public/{tasks,recorder}` | Recorded walkthrough of both gates | 1–2 |
| 9 | **Evaluation harness.** Milestone 0 tooling on synthetic CRM; frozen dev/test set layout; per-commit numbers never combined across revisions; harness advertised only after a recorded device self-test. | `scripts/cu_eval.py`, `tests/fixtures/cu/` | Report template committed; M1 (≥ 3 recordings × ≥ 5 tasks × 2 real apps × ≥ 2 people) is a **data-collection blocker owned by you**, not engineering | 0.5 |

Total ≈ 13–16 sessions of engineering, of which ≈ 3 are done; external waits: a macOS machine for step 5, and real recordings for milestone 1 (schedule people from step 3 onward — every provisional number in §2 is replaced by M1).

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

3. The recorder captures state through the same sidecar `observe()` the run loop uses (§2b gap 1); step 4 therefore precedes step 3.
4. Capture everything on device (§2b, step 2c); upload only the normalised graph and the existing metadata (§7). The two are governed separately and the leakage gate is the boundary between them.

## 7. Two statements: what is recorded, what is uploaded

The recorder consent card (`recorder/ui/dashboard.html`) and the website currently say:

> Activity metadata includes app names, interaction types/counts and timestamps. It excludes typed text, clipboard contents, URLs, window titles, screenshots, video and raw event logs.

Read as a description of the *recording* that sentence is wrong after step 2c and is replaced; read as the *upload* contract it stays exactly true. Under `computer-use-v2` the card says both halves explicitly:

> On this device, the recording captures everything you do in the apps you are recording: keys and text typed, clipboard, URLs and window titles, screenshots and screen video, files opened, created and modified, mouse and app activity. It stays on this device for your review. Sign-in and private windows are never recorded, and you can exclude any app or moment with one tap.
>
> What is sent to Vista is activity metadata — app names, interaction types/counts and timestamps — and, when you share a plan, the task's structure. It excludes typed text, clipboard contents, URLs, window titles, screenshots, video and raw event logs.

The second paragraph is the `activity-metadata-v1` upload contract unchanged; accepting both paragraphs is consent `computer-use-v2`, which the recorder stores on connect and without which no plan artifact is packaged (device) or accepted (cloud), and every "Leaves device" cell in §2b conforms to it. It is enforced, not described: the step-2 leakage check (`taskmining.leakage.check`) fails any cloud-bound payload containing an exact recorded value, a non-vocabulary AX name or a window title, and step 2b runs it on every producer in CI with seeded fixtures.

v3 adds exactly one category to what leaves the device — the compiled task graph ("the task's structure" above: which screens were visited, which kinds of controls were used and in what order, the names of the fields involved — never their contents). Precisely, the additions are: L0 sets (`have:/read:/open:/in:/ctx:` — slot *names*, screen-class *hashes*), L1 shape (landmark roles, modal flag, primary-button descriptor role + normalised name, control-class presence), at most 40 control descriptors `(role, normalised name, landmark, position class)` whose names pass the control-vocabulary rule, and redacted declared-output slot values. Still excluded, by the same check: typed text, clipboard text or hashes, URLs (only the path *shape* hash), window titles (only the title *shape* hash on desktop), screenshots, video, raw event logs, coordinates, selectors, file paths and page text. Known limits of the normaliser (single lower-case surnames, non-Latin scripts, letters-only identifiers) are printed in the compile report and on the consent card, not hidden.

Ship rule: the website and consent-card text (both paragraphs), the `consent_version` the recorder writes into `manifest.json`, and the leakage-check fixtures change in the same PR (step 2b), and a recording made under an older consent version never uploads plan data.

## 8. End state

When the breakdown in §3 is complete:

- An employee records a task once — everything in the mind map, in full, on the device — reviews a storyboard of screens and moves (never hashes) built from that full recording, fixes slot names, marks the goal frame, and shares the compiled plan under `computer-use-v2`. The recording itself never leaves the device.
- The device sidecar has already compiled it into `task = nodes + edges + goal + criteria` using the same `observe()` the agent will run with; the compile report shows provenance, slot alignment evidence, the leakage result and coverage. Nothing in the report or the graph contains a value, title, URL, path, coordinate or text.
- An FDE approves one task (≤ 25 edges) from that report and the shadow report; it becomes an approved, immutable version visible on the admin dashboard (#11) with every edge's policy, tier, statistics and provenance.
- Runs locate by L0 equality, resolve targets in code, ask Jev only among the located node's own edges, pause on every committing move, recover without ever clicking an affirmative, and verify writes by read-back. Every run folds its statistics into a draft; structure and permissions change only through an approved version.
- Promotion `shadow → ask → confirm → unattended` is per edge, proposed by code from measured thresholds (M1 numbers, not the provisional ones in §2), clicked by an FDE after cooling; demotion is automatic. A committing edge without covering read-back never reaches `unattended`.
- Browser (browser-use) and macOS (macOS-use) harnesses are advertised only after a recorded self-test on the device class; Linux desktop and Windows are stated unsupported until they pass one.
- Milestone 2 numbers (frozen dev/test sets, recording-compiled graphs only, ≥ 1 write task per app, per-commit) are the only performance claims made anywhere.
