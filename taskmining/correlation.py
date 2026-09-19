"""Case correlation: assign a business case id to each step.

Task mining has no natural case notion; Celonis derives it by extracting a
business object identifier (invoice number, ticket id, order number) from
window titles / URLs / clipboard and propagating it to neighbouring steps in
the same session that carry no identifier of their own.
"""

from __future__ import annotations

import re
from datetime import timedelta

from taskmining.models import Step

DEFAULT_ID_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bINV-\d{4,}\b"),
    re.compile(r"\bTICKET-\d+\b", re.I),
    re.compile(r"\bPO\d{6,}\b"),
]


def extract_case_id(text: str, patterns: list[re.Pattern[str]] | None = None) -> str:
    for p in patterns or DEFAULT_ID_PATTERNS:
        m = p.search(text)
        if m:
            return m.group(0)
    return ""


def correlate(
    steps: list[Step],
    patterns: list[re.Pattern[str]] | None = None,
    max_gap: timedelta = timedelta(minutes=5),
) -> list[Step]:
    """Fill ``Step.case_id`` in place.

    1. Direct: id found in the step's own title/url.
    2. Forward fill: carry the previous step's id within a session while the
       time gap is small (e.g. SAP screens without the invoice in the title).
    3. Backward fill: a step preceding the first identified step in a session
       (e.g. opening the mail client) inherits the upcoming id.
    """
    for s in steps:
        s.case_id = extract_case_id(f"{s.window_title} {s.url}", patterns)

    by_session: dict[str, list[Step]] = {}
    for s in steps:
        by_session.setdefault(s.session_id, []).append(s)

    for seq in by_session.values():
        seq.sort(key=lambda s: s.start)
        prev: Step | None = None
        for s in seq:
            if not s.case_id and prev and prev.case_id and s.start - prev.end <= max_gap:
                s.case_id = prev.case_id
            prev = s
        nxt: Step | None = None
        for s in reversed(seq):
            if not s.case_id and nxt and nxt.case_id and nxt.start - s.end <= max_gap:
                s.case_id = nxt.case_id
            nxt = s
    return steps
