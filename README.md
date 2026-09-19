# Vista

An open, dependency-free (Python 3.12+) reimplementation of the **Celonis Task Mining** pipeline:
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
| 3b. Human annotation *(Vista addition)* | — (Celonis only has analyst-side activity definitions and a client pause button) | `taskmining.annotations` | Employees or analysts state what happened in a time range (`Annotation(user, start, end, label, note, case_id, author)` from JSONL/CSV). Annotations outrank rules: overlapping steps are split and relabelled, annotated time with no screen events becomes an explicit `(off-screen)` step (phone, paper, meetings), and unexplained `Other (…)` steps / long gaps are turned into guided-interview `Question`s. |
| 4. Case correlation | "Case ID mapping" / business-object linking | `taskmining.correlation` | Extracts business identifiers (`INV-…`, `TICKET-…`, `PO…`) from titles/URLs and forward/backward-fills them within a session so screens that don't show the ID still land in the right case. Human-supplied case IDs are never overwritten; steps that still have no ID fall back to time-boxed **task episodes** (`EP-<user>-<n>`) so ID-less Excel/Outlook shops still get cases. |
| 5. Event log | Data model / Data Pool tables | `taskmining.eventlog` | Exports `case_id, activity, start, end, user, …` plus `activity_source`, `case_source` and `note` as CSV and IEEE XES so any process mining tool (Celonis, PM4Py, ProM, Disco) can load it. |
| 6. Discovery | Process Explorer, Variant Explorer | `taskmining.discovery` | Directly-follows graph with frequency and mean wait per edge, variants with throughput time, per-activity duration stats, rework detection. DOT and Mermaid renderers. |
| 7. Analytics | Automation opportunity / Task Mining dashboards | `taskmining.analytics` | Scores each activity on frequency, regularity (low duration CV), data transfer (copy/paste/typing volume) and app switching to rank automation candidates. |

`taskmining.pipeline.Pipeline` wires the stages together; `PipelineResult.write()`
produces `raw_events.jsonl`, `clean_events.jsonl`, `event_log.csv`,
`event_log.xes`, `dfg.dot`, `dfg.mmd`, `annotations.jsonl`, `questions.json` and
`summary.json`.

### Provenance

Every `Step` carries where its label and case came from, so observed facts stay
separable from inference and from what people told us:

| `Source` | `activity_source` means | `case_source` means |
|---|---|---|
| `observed` | — | id read directly from a window title / URL |
| `rule` | an `ActivityRule` matched | — |
| `fallback` | no rule matched → `Other (<app>)` | — |
| `filled` | — | propagated from a neighbouring step in the session |
| `episode` | — | synthetic case from a contiguous burst of work (no id available) |
| `human` | employee / analyst annotation | case id stated in an annotation |

`summary.json` reports the distribution of both, plus `off_screen_hours` and the
number of open questions for the next interview.

### Design notes

* **Task mining has no natural case.** The hard part—and the one Celonis hides
  behind configuration—is deciding which clicks belong together. Vista makes
  that explicit: sessions (idle gap) bound the search, identifier regexes
  anchor it, and fill rules extend it to neighbouring screens. Steps that still
  can't be correlated become task episodes (or, with `--no-episodes`, keep an
  empty `case_id` and are excluded from discovery).
* **Abstraction is rule-based on purpose.** Celonis lets analysts define
  activities from screen titles; ML labelling is layered on top. Rules are
  deterministic and auditable, which is what a process analyst needs first.
* **Privacy happens before anything is stored.** Redaction runs on the raw
  stream; keystroke text is redacted again after burst aggregation so an email
  typed one key at a time is still caught. Annotation notes are redacted and
  annotation users pseudonymised with the same salt as the events.
* **Screens are one evidence source, not the only one.** Phone calls, paper
  records and meetings never reach a recorder. Annotations put that work into
  the same event log as explicit off-screen steps, and off-screen steps never
  inherit a neighbour's case id — only a person can attach them to a case.

## Getting started

```bash
pip install -e . pytest ruff   # or: uv sync
pytest tests/test_pipeline.py

# generate raw events, then process them (mirrors client -> server hand-off)
python -m taskmining generate --cases 100 --out raw.jsonl
python -m taskmining run --input raw.jsonl --out out/

# add what the recorder cannot see (phone, paper, meetings, corrections)
python -m taskmining generate --cases 100 --out raw.jsonl --annotations-out ann.jsonl
python -m taskmining run --input raw.jsonl --annotations ann.jsonl --out out/
#   or: python -m taskmining run --synthetic 40 --annotations auto --out out/

# render the process graph
dot -Tpng out/dfg.dot -o dfg.png
```

Annotation files are JSONL (one object per line) or CSV with columns
`user,start,end,label[,note,case_id,author]`; timestamps are ISO-8601 and `user`
is the raw user name (it is pseudonymised on the way in). Run the pipeline once,
hand `out/questions.json` to the employee or analyst, append their answers as
annotations and run again.

Plug in a real recorder by implementing `EventSource.events()` and yielding
`RawEvent`s, and add `ActivityRule`s / case-id patterns for your applications.
