"""Event log export in CSV and XES (IEEE 1849) formats."""

from __future__ import annotations

import csv
from typing import TextIO
from xml.sax.saxutils import escape, quoteattr

from taskmining.models import Step

CSV_COLUMNS = [
    "case_id",
    "activity",
    "start",
    "end",
    "duration_s",
    "user",
    "session_id",
    "app",
    "n_events",
    "n_keys",
    "n_copies",
    "n_pastes",
    "n_transfers",
    "activity_source",
    "case_source",
    "note",
]


def to_csv(steps: list[Step], fp: TextIO) -> None:
    w = csv.writer(fp)
    w.writerow(CSV_COLUMNS)
    for s in steps:
        w.writerow(
            [
                s.case_id,
                s.activity,
                s.start.isoformat(),
                s.end.isoformat(),
                f"{s.duration_s:.3f}",
                s.user,
                s.session_id,
                s.app,
                s.n_events,
                s.n_keys,
                s.n_copies,
                s.n_pastes,
                s.n_transfers,
                s.activity_source.value,
                s.case_source.value if s.case_source else "",
                s.note,
            ]
        )


def to_xes(steps: list[Step], fp: TextIO) -> None:
    cases: dict[str, list[Step]] = {}
    for s in steps:
        cases.setdefault(s.case_id or "UNCORRELATED", []).append(s)

    fp.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    fp.write('<log xes.version="1849.2016" xes.features="nested-attributes" xmlns="http://www.xes-standard.org/">\n')
    fp.write('  <extension name="Concept" prefix="concept" uri="http://www.xes-standard.org/concept.xesext"/>\n')
    fp.write('  <extension name="Time" prefix="time" uri="http://www.xes-standard.org/time.xesext"/>\n')
    fp.write('  <extension name="Organizational" prefix="org" uri="http://www.xes-standard.org/org.xesext"/>\n')
    fp.write('  <classifier name="Activity" keys="concept:name"/>\n')
    for cid, seq in cases.items():
        fp.write("  <trace>\n")
        fp.write(f'    <string key="concept:name" value={quoteattr(cid)}/>\n')
        for s in sorted(seq, key=lambda x: x.start):
            fp.write("    <event>\n")
            fp.write(f'      <string key="concept:name" value={quoteattr(s.activity)}/>\n')
            fp.write(f'      <string key="org:resource" value={quoteattr(s.user)}/>\n')
            fp.write(f'      <date key="time:timestamp" value="{escape(s.start.isoformat())}"/>\n')
            fp.write(f'      <date key="time:end" value="{escape(s.end.isoformat())}"/>\n')
            fp.write(f'      <string key="app" value={quoteattr(s.app)}/>\n')
            fp.write(f'      <int key="n_keys" value="{s.n_keys}"/>\n')
            fp.write(f'      <int key="n_pastes" value="{s.n_pastes}"/>\n')
            fp.write(f'      <int key="vista:n_transfers" value="{s.n_transfers}"/>\n')
            fp.write(f'      <string key="vista:activity_source" value={quoteattr(s.activity_source.value)}/>\n')
            if s.case_source:
                fp.write(f'      <string key="vista:case_source" value={quoteattr(s.case_source.value)}/>\n')
            if s.note:
                fp.write(f'      <string key="vista:note" value={quoteattr(s.note)}/>\n')
            fp.write("    </event>\n")
        fp.write("  </trace>\n")
    fp.write("</log>\n")
