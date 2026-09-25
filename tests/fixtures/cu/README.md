# Computer-use evaluation sets

`dev/` and `test/` each hold a `set.json` manifest and one JSON case per recording-compiled
graph (format in `src/taskmining/evaluate.py`). Graphs come from the recorder's compiler only;
the harness rejects anything else. `kind: synthetic` cases (the synthetic CRM / invoice fixture)
are development smoke and never enter benchmark totals.

- `dev/` — may change; used while developing.
- `test/` — frozen with `uv run python scripts/cu_eval.py freeze test`; a changed case fails
  `load_set`. Milestone 1 fills it: ≥ 3 recordings × ≥ 5 tasks × 2 real apps from ≥ 2 people.
  Held-out frames must come from recordings the graph was **not** compiled from.

Run `make cu-eval SET=dev`; reports go to `eval/cu/<set>-<revision>.{json,md}` — one revision per
report, never combined across revisions.
