# Vista

Process intelligence for lower-middle-market companies: record how work actually
gets done on employees' desktops, turn the clicks into a process map, and rank
what to automate first — with every number tied back to observed evidence.

```
clicks → steps → cases → process map → dollars
```

## Structure

```
src/
  recorder/     Electron desktop app the employee runs (one button, always-on overlay)
  taskmining/   Python engine: redact → sessionise → abstract → annotate → correlate → log → discover → score
  vista/        FastAPI backend: tenants, deals, employee agents, findings (Postgres + S3)
tests/          pytest (test_pipeline.py = engine; the rest need Postgres)
migrations/     Alembic, platform + per-tenant schemas
synthetic_data/ generated company datasets for the backend agents
scripts/        demo.py — backend end-to-end smoke run
setup.sh, Makefile   one-command local setup and day-to-day commands
```

Each component has its own README: [`src/recorder`](src/recorder/README.md),
[`src/taskmining`](src/taskmining/README.md).

## How the pieces connect

```
 employee desktop                                              analyst / company
┌───────────────────────────────────────────────────┐
│ recorder (Electron)                               │
│  hooks + screenshots + clipboard → redact on device│
│  → ~/Vista/recordings/<id>/events.jsonl           │
│  on Stop: python -m taskmining run ──► processed/ │──► dashboard "Last session"
└───────────────────────────────────────────────────┘
                     │ (not wired yet)
                     ▼
┌───────────────────────────────────────────────────┐
│ vista backend (FastAPI + Postgres + S3)           │
│  tenants · deals · employee agents · findings     │──► company portal / PE-fund views
└───────────────────────────────────────────────────┘
```

* `recorder` writes `RawEvent` JSONL — the exact shape `taskmining` reads — and
  runs the engine locally when a recording stops. Everything stays on the
  employee's machine today.
* `taskmining` is pure functions over event lists; it has no I/O beyond reading
  and writing files, so the same code will run as a queue worker later.
* `vista` (backend) is **not connected to the recorder yet**. It has no
  task-mining endpoints; the planned hand-off is batched JSONL upload →
  ingest API → per-tenant event-log store → findings. Until then the backend
  and the recorder/engine are developed independently.

## Getting started

Needs [uv](https://docs.astral.sh/uv/) and Node 18+ (`brew install uv node`).
`./setup.sh --check` tells you what is missing and how to install it.

```bash
git clone https://github.com/ylemiesa57/vista.git && cd vista
./setup.sh          # uv sync + npm install + ruff/pytest/node tests
make demo           # recorder with simulated apps, no OS permissions needed
make start          # real recorder (macOS asks for Accessibility/Input Monitoring/Screen Recording)
```

| Command | What |
|---|---|
| `make test` | ruff + engine pytest + recorder node tests |
| `make run` | engine on 40 synthetic cases → `out/` |
| `make demo` / `make start` | recorder |
| `make lint` / `make fmt` | ruff |

No Docker for the recorder/engine: the recorder has to run on the employee's
own desktop and the engine is stdlib-only. The backend does use
`docker compose up -d` for Postgres and MinIO (`.env.example`), then
`uv run pytest` runs its tests and `uv run python scripts/demo.py` a smoke run.
