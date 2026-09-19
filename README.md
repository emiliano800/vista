# Vista

An open, dependency-free reimplementation of the **Celonis Task Mining** pipeline:
from raw desktop interactions to a process-mining event log, discovered process
model, and automation-potential ranking.

```
python -m taskmining run --synthetic 40 --out out/
```

## How Celonis Task Mining works (reverse-engineered)

Celonis Task Mining is a "bottom-up" complement to process mining. Instead of
reading transactional data out of ERP tables, a desktop client watches how
people actually work across applications and turns the clicks into an event
log that the same discovery engine (Process Explorer, Variant Explorer,
Conformance) can consume. Public documentation, marketing material and the
behaviour of the product point to the following stages, each of which has a
module here:

| Stage | Celonis component | Vista module | What it does |
|---|---|---|---|
| 1. Capture | Task Mining desktop client (Windows) | `taskmining.capture` | Hooks OS input + UI Automation/accessibility APIs to record `click`, `key`, `focus`, `copy`, `paste`, `scroll` with app, window title, URL and UI element. Vista defines the `EventSource` contract, a `JsonlSource`, and a `SyntheticSource` that emits realistic accounts-payable traffic. |
| 2. Privacy & pre-processing | Client-side "data protection" rules, server-side cleaning | `taskmining.preprocess` | Regex PII redaction (email, phone, IBAN, card), user pseudonymisation (salted SHA-256), keystroke-burst aggregation (single keys collapsed into one typing event with `n_keys`), and idle-gap sessionisation. |
| 3. Activity abstraction | "Activity definitions" / task labelling in the Task Mining app | `taskmining.abstraction` | Ordered `ActivityRule`s matching app / title / URL / element / event type lift low-level events to business activities (`Enter Invoice (SAP MIRO)`), merging consecutive events of one activity into a `Step` with counts of keys, copies and pastes. |
| 4. Case correlation | "Case ID mapping" / business-object linking | `taskmining.correlation` | Extracts business identifiers (`INV-…`, `TICKET-…`, `PO…`) from titles/URLs and forward/backward-fills them within a session so screens that don't show the ID still land in the right case. |
| 5. Event log | Data model / Data Pool tables | `taskmining.eventlog` | Exports `case_id, activity, start, end, user, …` as CSV and IEEE XES so any process mining tool (Celonis, PM4Py, ProM, Disco) can load it. |
| 6. Discovery | Process Explorer, Variant Explorer | `taskmining.discovery` | Directly-follows graph with frequency and mean wait per edge, variants with throughput time, per-activity duration stats, rework detection. DOT and Mermaid renderers. |
| 7. Analytics | Automation opportunity / Task Mining dashboards | `taskmining.analytics` | Scores each activity on frequency, regularity (low duration CV), data transfer (copy/paste/typing volume) and app switching to rank automation candidates. |

`taskmining.pipeline.Pipeline` wires the stages together; `PipelineResult.write()`
produces `raw_events.jsonl`, `clean_events.jsonl`, `event_log.csv`,
`event_log.xes`, `dfg.dot`, `dfg.mmd` and `summary.json`.

### Design notes

* **Task mining has no natural case.** The hard part—and the one Celonis hides
  behind configuration—is deciding which clicks belong together. Vista makes
  that explicit: sessions (idle gap) bound the search, identifier regexes
  anchor it, and fill rules extend it to neighbouring screens. Steps that can't
  be correlated are kept in the CSV with an empty `case_id` and excluded from
  discovery.
* **Abstraction is rule-based on purpose.** Celonis lets analysts define
  activities from screen titles; ML labelling is layered on top. Rules are
  deterministic and auditable, which is what a process analyst needs first.
* **Privacy happens before anything is stored.** Redaction runs on the raw
  stream; keystroke text is redacted again after burst aggregation so an email
  typed one key at a time is still caught.

## Getting started

```bash
pip install -e ".[dev]"
pytest

# generate raw events, then process them (mirrors client -> server hand-off)
python -m taskmining generate --cases 100 --out raw.jsonl
python -m taskmining run --input raw.jsonl --out out/

# render the process graph
dot -Tpng out/dfg.dot -o dfg.png
```

Plug in a real recorder by implementing `EventSource.events()` and yielding
`RawEvent`s, and add `ActivityRule`s / case-id patterns for your applications.
