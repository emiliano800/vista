# recorder — the desktop app

The employee-facing recorder: one Start button, an always-on-top overlay pill
that stays visible over Excel/Outlook/anything, and a small dashboard (today,
my recordings, what is recorded, settings). Electron shell, `uiohook-napi` for
global mouse/keyboard/shortcut hooks, `get-windows` for the foreground app and
window title, `desktopCapturer` for screenshots and a low-frame-rate
`screen.webm`.

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
processed/        output of `taskmining run`, executed automatically on Stop
```

**Submit** uploads the report, then every file above to the workspace
(`<tenant>/deals/<deal>/recordings/<id>/media/…`), then deletes the folder.
Only a metadata stub stays in `~/Vista/submitted/<id>/`; section labels are kept
three ways — in the recording row (`Recording.sections`), in
`screen.sections.json` beside the video, and in the stub.

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

**Cloud review (`src/cloud.js`).** With Settings → Cloud workspace filled in
(website URL, company ID, access key), Stop uploads the redacted report
(`manifest.json`, `processed/summary.json`, `processed/event_log.csv` — never
`events.jsonl`, shots or video) to `POST /api/deals/{company}/recordings`, then
`PUT /api/recordings/{id}/review/sections` with each section's metadata and
redacted description. The backend's job worker asks OpenAI and the recorder
polls `GET …/review` (`REVIEW_POLL_MS`, up to `REVIEW_POLL_MAX_MS`), merging
the result into `review.json` with `source: "cloud"`. Approve / Fix / Explain
post to `POST …/review/{item}` and are kept locally if the workspace is
unreachable (`sync_error`). Employees never hold an OpenAI key in this mode.

Without a workspace, AI explanations run locally and need `OPENAI_API_KEY` (or
Settings → AI explanations); `VISTA_OPENAI_MODEL` (default `gpt-4o-mini`) and
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
