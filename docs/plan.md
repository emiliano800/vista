# Plan: agent suite on synthetic data — build and test order

Status (2026-09-23): steps 1–4 shipped as written. Steps 5–7 were superseded by the
canonical import contract (`src/vista/portfolio/imports.py`), the interpretation
chain (`portfolio/interpret.py`) and the analyst workspace; Propose/Execute remain
test/eval-only. Kept as the reference for the phase shape and test tiers.

Scope for this PR: the smallest vertical slice that lets every agent phase be
developed and tested without spending tokens, plus the two on-demand paths that
do spend tokens (provider smoke test, answer-key eval). No MCP, no external
connectors; `synthetic_data/` is the only data source.

## Shape of an agent phase

Every phase is three pure functions so that only the middle one needs a model:

```
prepare(inputs)  -> Prompt(system, user, max_tokens)     # code: reads synthetic tables, computes stats
chat(prompt)     -> ChatResult(model, text, tokens)      # model (stub / cassette / live)
parse(text)      -> pydantic output                      # code: strict schema, tolerant of fences
apply(output, ctx) -> rows / actions                     # code: permissions, dedupe, persistence
```

`runtime.run_phase()` wires them and returns prompt + usage + parsed output so
tests can assert on any stage.

| Stage    | Kind (code)          | prepare reads                                        | apply writes                          |
|----------|----------------------|------------------------------------------------------|---------------------------------------|
| Discover | `file_reviewer`      | one table (csv / xlsx sheet) + deterministic profile  | `observed_facts` (dataset, column refs) |
| Propose  | `config_proposer`    | facts for a company + company profile                | `proposals` (column_mapping, dedupe_merge, workflow_change) |
| Execute  | `division_executor`  | approved proposals + one division's tables           | `findings`, `tasks` only — scope-checked |
| Analyze  | `portfolio_analyst`  | one opportunity kind at a time, that kind's table types across a sector | `opportunities` → `proposed_automation` findings via `POST /synthetic/analyze`; must skip TRAP items |

## Test tiers (cheapest runs on every save; only tier 0 and 3 spend tokens)

| Tier | What                                   | Needs             | Command                          | When |
|------|----------------------------------------|-------------------|----------------------------------|------|
| 0 | provider smoke: `_chat` works, usage present, JSON mode parses | API key | `make llm-smoke` / `pytest -m live` | new model/provider, nightly |
| 1 | unit: `prepare`/`parse`/`apply` per phase with canned model text | nothing | `make test-agents` (`pytest -q tests/test_agents.py`) | every save |
| 2 | integration: import a synthetic folder → job → worker → ledger rows | Postgres (`make db`) | `pytest -q tests/test_agents_db.py` | before push |
| 3 | eval: run live (or replay a cassette) on a company and score against `answer_key.json` | API key or cassette | `make eval COMPANY=ridgeway DIVISION=11_billing_ar`, `make eval PHASE=analyze SECTOR=industrial_goods` | before merging prompt / context changes |

Tier-1 rules: `conftest.py` already forces `openai_api_key = None`, so `chat()`
returns the stub. Tests that need model text monkeypatch `chat` with a fixture
string; they never construct an OpenAI client.

Tier-3 rules: `answer_key.json` is loaded only by `vista.agents.eval`; no phase
module imports it. A prediction matches an item when kind, company and at least
one evidence file agree. Matching a `is_false_positive_trap` item counts as a
false positive. Live responses are recorded to a JSON cassette keyed by prompt
hash so the same run replays in CI for free; re-record when a prompt changes.

## Delivery order (this PR = steps 1–4)

1. `vista.agents.synthetic` loader (manifest → companies → tables; csv + xlsx sheets; answer key).
2. `vista.agents.llm` — `chat()` with stub / cassette / live; `scripts/llm_smoke.py`; `live` marker.
3. Phase modules + `runtime.run_phase`; unit tests with fixtures from the low-tier companies
   (Ridgeway swapped SO/PO columns, mislabelled `MAX`, Castlebrook duplicate insureds).
4. `synthetic_discovery` job handler reusing `agent_runs` / `agent_run_events` / `usage_events` / `findings`;
   DB integration test; `vista.agents.eval` scorer + `scripts/eval_agents.py`; Makefile targets.
5. (next PR) `companies` / `datasets` / `records` / `workflow_events` tables and the import command.
6. (next PR) Propose + Execute persisted (`proposals`, `actions`, `exceptions`, `tasks`) and review API.
7. (next PR) Portfolio workspace + CFO dashboard (fleet, run trace, spend by company × division × model, eval score).
