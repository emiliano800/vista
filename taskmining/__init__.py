"""Vista task mining: a reverse-engineered, open implementation of the
Celonis Task Mining pipeline.

Stages (mirroring the Celonis architecture):

1. capture      - desktop client records raw UI interactions (click, key,
                  window focus, clipboard) per user.
2. preprocess   - PII redaction, keystroke burst aggregation, idle-gap
                  sessionization.
3. abstraction  - low-level UI events are lifted to business activities via
                  window/app rules.
4. correlation  - activities are grouped into cases by extracting business
                  object identifiers (invoice / ticket numbers) from window
                  titles, URLs and clipboard.
5. eventlog     - export to a process-mining event log (CSV / XES).
6. discovery    - directly-follows graph, variants, throughput metrics.
7. analytics    - automation potential scoring per activity / variant.
"""

from taskmining.pipeline import Pipeline, PipelineResult

__all__ = ["Pipeline", "PipelineResult"]
