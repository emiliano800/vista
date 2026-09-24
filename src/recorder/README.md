# recorder — the desktop app

The employee-facing recorder: one Start button, an always-on-top overlay pill
that stays visible over Excel/Outlook/anything, and a small dashboard (today,
my recordings, what is recorded, settings). Electron shell, `uiohook-napi` for
global mouse/keyboard/shortcut hooks, `get-windows` for the foreground app and
window title, `desktopCapturer` for screenshots and a low-frame-rate
`screen.webm`.

## Role in the Vista platforms

The recorder is the employee evidence-collection and verification surface. Vista's
management product is split into a financial platform (PE analyst and portco CFO)
and an automation platform (FDE, or forward-deployed engineer).

- **FDE:** use permitted recording evidence, employee explanations, process timings,
  and cross-app transfers to understand workflows, propose automations, and assess
  operational results. The dedicated FDE workspace is planned, not implemented here.
- **Portco CFO:** see the financial implications and supporting evidence for their
  company as a subset of the analyst's financial view.
- **PE analyst:** compare financial performance and validated impact across authorized
  portfolio companies. Detailed workflow debugging is primarily FDE work.

Recorded repetition or estimated time saved is an automation candidate, not proof
of realized financial savings. Future links from workflow results to the financial
model must preserve the baseline, assumptions, provenance, and review decisions.
Recording access remains explicitly scoped; being an analyst or CFO does not itself
imply unrestricted access to employee media. The upload/review behavior below is the
current implementation and must be considered when defining those permissions.

## Capture and local artifacts

Screenshots are taken on window switch, every 15 s, and **when the screen
actually changes**: a 64x36 grayscale thumbnail is polled every 750 ms and a
full frame is shot when >= 4 % of its pixels move (min 1.5 s apart), so a new
email, a new Excel sheet or a dialog inside the same window is captured. The
`screen` event carries `payload.reason` (`focus` / `interval` / `change`) and
the normalised `diff`.

Copy and paste are **linked**: on Cmd/Ctrl+C the recorder remembers a salted
hash of the clipboard plus the source app/window; a later Cmd/Ctrl+V with the
same clipboard gets `payload.source_app`, `source_title`, `transfer_ms` and
`cross_app`. The engine counts cross-app pastes as `n_transfers` per step
(weighted in the automation score) and aggregates them into `data_flows`
("Acrobat -> QuickBooks, 42x") in `summary.json` - the swivel-chair map.

Everything is written locally to `~/Vista/recordings/<id>/`:

```
events.jsonl      one RawEvent per line - the same wire format taskmining reads
manifest.json     user, platform, start/end, counts, app time, processing state
shots/NNNNNN.jpg  screenshots referenced by `screen` events (payload.image)
screen.webm       optional screen video
annotations.jsonl notes the employee adds + approved/fixed AI explanations (author, ai provenance)
review.json       the AI's explanation per section and the employee's decision on each
sections.json     the employee's edits per section (name, note, edited_at)
screen.sections.json  written on Submit: every section resolved (span, video offset,
                  name, note, AI explanation + decision, annotations) — the video's
                  label track, uploaded next to screen.webm
files.json        documents seen during the session (macOS): open/close intervals per
                  file, Spotlight/Downloads sightings, sha256 + size of the snapshot
files/<id>/<name> the *last* version of each document, copied once at Stop
processed/        output of `taskmining run`, executed automatically on Stop
```

**Submit** (legacy protocol 1; a connected app uses **Upload session** below)
uploads the report, then every file above to the workspace
(`<tenant>/deals/<deal>/recordings/<id>/media/…`), then deletes the folder.
Only a metadata stub stays in `~/Vista/submitted/<id>/`; section labels are kept
three ways — in the recording row (`Recording.sections`), in
`screen.sections.json` beside the video, and in the stub.

**Documents (`src/files.js`, macOS only).** Every 5 s while recording, and on
every window switch, the recorder asks the frontmost window for its document
(Accessibility `AXDocument`; `lsof -p` as a fallback for apps without it) and
turns the answers into per-file open/close intervals, cut around pauses just
like the video. At Stop a Spotlight sweep (`kMDItemLastUsedDate`,
`kMDItemDateAdded` within the session) adds anything the probes missed —
including new downloads — and the current version of each allowlisted file
(xlsx/xls/csv/docx/pptx/pdf/txt/md/json/xml, ≤ 50 MB) is copied once into
`files/<id>/`. Nothing is read while recording; `~/Library`, caches and the
recorder's own folders are never looked at. The review shows a document track
under the scrubber (one bar per open interval, ticks for Spotlight sightings)
and the file on screen at the playhead; the employee can untick a file before
Submit, which deletes its snapshot. Absolute paths never leave the machine —
the report carries name, folder, markers and hash. Server-side, the
`extract_recording_files` job turns each snapshot into bounded JSON
(sheets/rows, paragraphs, slides, PDF text; `vista/documents.py`) that the
workspace serves at `GET /recordings/{id}/files/{file}/text`.

**Review after Stop.** The dashboard splits the session into *sections* — the
longest stretches in one window, with quick hops (a 4-second Outlook check in
the middle of Excel work) folded into the surrounding span
(`src/sections.js`, config in `SECTION_DEFAULTS`). Sections are ranges into the
one `screen.webm` (`offset_s`, paused time excluded), not separate files; each
card plays its stretch.

**AI explanations (`src/explain.js`).** When a key is configured, Stop also
asks the model to explain every section and the whole session from its
metadata (apps, titles, counts, copy→paste flows, shortcuts — never
keystrokes, screenshots off by default). Each answer is
`{label, explanation, confidence, unclear, questions}` and lands in
`review.json` with a status:

| confidence | status | employee sees |
|---|---|---|
| ≥ `CONFIDENCE_THRESHOLD` (0.88) | `proposed` | label + explanation, **Approve** / **Fix** |
| < 0.88 | `unsure` | what the AI was unsure about + its questions, **Explain what happened** (required) |
| request failed | `failed` | treated like `unsure` |

Approve / Fix / Explain (`applyDecision`) move the item to
`approved` / `fixed` / `explained` and append an annotation
(`scope: "section"`, `author: "ai"` for approvals, `"employee"` otherwise,
plus the model's original under `ai`) so the taskmining pass and the analyst
keep observed vs. AI vs. human facts apart. The banner's analyst summary
(`reviewSummary`) counts how many sections were unclear, how many still need
approval and how many are confirmed. The whole-session item writes a
`scope: "session"` summary that names the recording
(`Tue 09:00–11:30 · Excel, Outlook, SAP — Month-end AP run`) without
relabelling individual steps. Without a key the cards fall back to a plain
**Describe** note. Pause/Resume on the overlay pauses hooks and video together;
pause intervals are stored in `manifest.json`.

IPC surface (renderer → main, `preload.cjs`): `sections(id)` returns sections
with their `review` item plus `review.summary`; `explain(id, {force})`
(re)generates; `decide(id, itemId, action, {label, note, answers})` applies
Approve/Fix/Explain; `onSections` streams updates while the model runs.

**Upload session (`src/intake.js`, protocol 2 — what a connected app uses).**
The first launch is a one-time setup screen (there is no Settings view): it
takes a personal access key, fetches the workspaces that key may upload to
(`GET /api/recorder/workspaces`), and the employee picks one explicitly. Once
connected the screen never returns unless the stored connection must be
upgraded. Capture options are not configurable — every capture setting in
`DEFAULT_SETTINGS` is on and `loadSettings` ignores saved overrides for them —
because what leaves the computer is decided at upload time, not at capture. After Stop, **Upload session** previews the
destination and the document snapshots, and the employee approves the sharing
package. The package is metadata only — `activity.json` with timestamps, app
names, interaction types and counts — plus the selected documents in full. No
window titles, URLs, typed text, clipboard, screenshots or video leave the
computer, and local originals are retained. A disk-backed queue
(`~/Vista/upload-queue/`) registers the submission, uploads each artifact with a
checksum-bound signed URL, and completes it; the server verifies sizes and
hashes and returns an idempotent receipt.

Acceptance queues the Recording Reviewer in the workspace. The app polls
`GET /api/recorder/submissions/{id}` every 30 s while the analysis runs. The
**Last session** screen is deliberately high level: one sentence about what
the session was (the workspace's summary once it exists, a plain local line
before that) and one action. The report's facts, hypotheses and tables are not
shown to the employee; they live in the workspace report. **Share with my
company** opens a dialog with the workspace's few optional questions and the
second, explicit consent; confirming posts any answers (`…/answers`) and then
publishes (`…/publish`), the only way the report becomes visible to the
company workspace. A failed analysis can be retried (`…/analyze`). The local
queue caches the server's answers under `analysis`; the server is the source
of truth. The local analysis (sections, flags, insights, workflows) still runs
after Stop and feeds annotations, but its cards are no longer rendered.

**Releases and macOS signing.** Pushing a `recorder-v*` tag runs
`.github/workflows/release-recorder.yml`, which builds the DMGs and the Windows
installer and attaches them to a GitHub release under stable names; the download
page links to `latest`. macOS builds are **ad-hoc signed** by default
(`mac.identity: "-"`), so Gatekeeper asks the employee to allow the app once via
System Settings → Privacy & Security → Open Anyway. Never ship a fully unsigned
build: Apple Silicon reports a quarantined unsigned app as "damaged". To ship a
build that opens without prompts, add the repository secrets `MAC_CSC_LINK`
(base64 Developer ID Application `.p12`), `MAC_CSC_KEY_PASSWORD`, `APPLE_ID`,
`APPLE_APP_SPECIFIC_PASSWORD` and `APPLE_TEAM_ID`; the workflow then signs with
the Developer ID, enables the hardened runtime and notarizes automatically.

**Cloud review (`src/cloud.js`, legacy protocol 1 — no longer reachable from the UI).** With an older connection file
(website URL, company ID, access key), Stop uploads the redacted report
(`manifest.json`, `processed/summary.json`, `processed/event_log.csv` — never
`events.jsonl`, shots or video) to `POST /api/deals/{company}/recordings`, then
`PUT /api/recordings/{id}/review/sections` with each section's metadata and
redacted description. The backend's job worker asks OpenAI and the recorder
polls `GET …/review` (`REVIEW_POLL_MS`, up to `REVIEW_POLL_MAX_MS`), merging
the result into `review.json` with `source: "cloud"`. Approve / Fix / Explain
post to `POST …/review/{item}` and are kept locally if the workspace is
unreachable (`sync_error`). Employees never hold an OpenAI key in this mode.

Without a workspace, AI explanations run locally and need `OPENAI_API_KEY`
(or `openaiApiKey` in `~/Vista/settings.json`); `VISTA_OPENAI_MODEL` (default `gpt-4o-mini`) and
`VISTA_OPENAI_URL` (any OpenAI-compatible chat-completions endpoint) are
optional. The key stays in the Electron main process.

Redaction (emails, phones, IBAN/card/SSN) runs on the device before a line is
written; typed characters are not stored by default (only key counts and
shortcut combos); private apps/title keywords mute capture entirely.

```bash
make demo                # simulated Outlook/Acrobat/QuickBooks/Excel activity, no hooks
make start               # real hooks
```

On Stop the recorder runs `python -m taskmining run` with the repo's `.venv`
(created by `uv sync` / `setup.sh`); set `VISTA_PYTHON=/path/to/python` to use
another interpreter.

### macOS

```bash
brew install uv node
./setup.sh && make start
```

macOS asks for three permissions the first time (System Settings → Privacy &
Security); the dashboard shows which are missing and opens the right pane:

| Permission | Why | Without it |
|---|---|---|
| Accessibility | `uiohook-napi` global hooks, foreground window | no clicks/keys, Start is blocked |
| Input Monitoring | keyboard events | key counts and shortcuts stay at 0 |
| Screen Recording | window titles (`get-windows`), screenshots, `screen.webm` | titles empty, no shots |

The app appears in the permission lists only after it has tried once, so press
Start, grant, then press Start again. In development the entry is "Electron";
a signed `.app` build (`electron-builder`) shows as "Vista". While recording
the Dock icon hides so only the overlay is visible; it returns on Stop.

## Computer use sessions and consent

The Computer Use Agent (see `AGENTS.md`) can carry out an *approved* sandbox workflow
through an employee's computer. The cloud never reaches the recorder; the recorder
pulls work and the employee starts every session here, by hand:

1. On the same 30 s tick as uploads, the recorder announces this device
   (`GET /api/recorder/computer-use/sessions?device_id&platform&browser&desktop&recorder_version`)
   with the harnesses this build can actually provide, and lists runs waiting for one.
2. **Computer use** in the dashboard shows those offers. *Start…* opens a plain-language
   notice; the session is claimed only after the consent box is ticked
   (`POST …/sessions/{run_id}/claim` with `consent: {version: "computer-use-v1", accepted_at,
   screenshots}`). Screenshots stay on this computer unless the second box is ticked.
3. While a session is active the recorder polls `GET …/sessions/{id}` every 3 s (the poll is
   also the lease heartbeat). Each step the server files is checked on this computer first
   (`src/computer-use/policy.js`): the harness kind must be one the employee consented to
   *and* one this build provides; the run's step and runtime limits are mirrored locally; only
   a closed set of actions with well-formed values is accepted; a targeted action must cite
   the observation its target came from, and that observation must still be current. A step
   that fails the check is reported back as refused and never performed.
4. Every request and result is appended to `~/Vista/computer-use/<session>/steps.jsonl`
   (mode 0600, never under `recordings/`) *before* the result is posted
   (`POST …/steps/{step_id}/result`). The lease token is never written to disk.
5. *Stop*, **⌘⇧Esc** (a global shortcut registered only while a session is active), or
   quitting the recorder ends the session (`POST …/sessions/{id}/stop`); a lost lease ends it
   locally. A session interrupted by a restart is listed as interrupted, never resumed silently.

**Drivers** (`src/computer-use/`): `browser.js` opens a visible window in the recorder's own
storage partition (`persist:vista-computer-use` — none of the employee's logins, cookies or
history) and drives it over CDP: candidates are the ≤40 named controls in the accessibility
tree, clicks go through the real pointer (`@nut-tree-fork/nut-js`, optional) or CDP input,
typing through `Input.insertText`. `desktop.js` reads the frontmost window's accessibility
tree and drives the real pointer/keyboard; only `desktop-macos.js` (System Events + nut-js,
needs Accessibility permission) exists, so other platforms advertise `desktop: false`. Every
targeted step must cite the observation it was chosen from and a control it enumerated; a
stale observation, a changed front window, a secure text field or a sign-in/payment/private
window fails closed (`stale_observation`, `sensitive_window`). A kind without a driver is an
`UnsupportedHarness` (`harnesses.js`): it advertises no capability, so the workspace never
offers such a run to this device, and a step that arrives anyway is answered
`harness_unsupported` — which the server treats as "leave this step for a person". In
`--demo` mode both kinds are unsupported-but-advertised so the whole protocol can be walked
through locally. Tests: `test/cu-policy.test.js`, `test/cu-client.test.js`,
`test/cu-drivers.test.js` (fake CDP page / AX backend), `npm run test:browser` (real Electron).

Over the agent API (`VISTA_RECORDER_API_TOKEN`): `GET /computer-use/status`,
`GET /computer-use/sessions` (presence + offers), `POST /computer-use/sessions/{run_id}/start`
(400 unless `consent: true`; `share_screenshots` optional), `POST /computer-use/sessions/{id}/stop`,
`GET /computer-use/sessions/{id}/steps`. An agent cannot manufacture consent: the route
refuses anything but the literal `true`, and the dashboard is the only place the notice is shown.
