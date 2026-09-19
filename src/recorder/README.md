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
annotations.jsonl notes the employee adds (phone, paper, meetings)
processed/        output of `taskmining run`, executed automatically on Stop
```

**Review after Stop.** The dashboard splits the session into *sections* — the
longest stretches in one window, with quick hops (a 4-second Outlook check in
the middle of Excel work) folded into the surrounding span
(`src/sections.js`, config in `SECTION_DEFAULTS`). Sections are ranges into the
one `screen.webm` (`offset_s`, paused time excluded), not separate files; each
card plays its stretch and can be **Described** (label + note, saved to
`annotations.jsonl` with `scope: "section"` and `section_id`) or handed to
**Ask AI**, which sends the section's metadata (apps, titles, counts,
copy→paste flows, shortcuts — never keystrokes, screenshots off by default) to
OpenAI and gets 2–4 clarifying questions back; answers are saved with the
note. The whole-session card writes a `scope: "session"` summary that names the
recording (`Tue 09:00–11:30 · Excel, Outlook, SAP — Month-end AP run`) without
relabelling individual steps. Pause/Resume on the overlay pauses hooks and
video together; pause intervals are stored in `manifest.json`.

Clarifying questions need `OPENAI_API_KEY` (or Settings → Clarifying
questions); `VISTA_OPENAI_MODEL` (default `gpt-4o-mini`) and
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
