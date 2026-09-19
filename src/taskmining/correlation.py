"""Case correlation: assign a business case id to each step.

Task mining has no natural case notion; Celonis derives it by extracting a
business object identifier (invoice number, ticket id, order number) from
window titles / URLs / clipboard and propagating it to neighbouring steps in
the same session that carry no identifier of their own.

Every assignment records its provenance in ``Step.case_source``:

* HUMAN    - stated in an annotation (never overridden here)
* OBSERVED - id found in the step's own title / url
* FILLED   - carried from a neighbouring step in the same session
* EPISODE  - no id anywhere: a contiguous burst of work in a session becomes
             its own synthetic case ``EP-<user>-<n>`` so that shops without
             ids in window titles (spreadsheets, paper, phone) still yield a
             task-level process model
"""

from __future__ import annotations

import re
from datetime import timedelta

from taskmining.models import OFF_SCREEN_APP, Source, Step

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
    episodes: bool = False,
) -> list[Step]:
    """Fill ``Step.case_id`` / ``Step.case_source`` in place.

    1. Direct: id found in the step's own title/url.
    2. Forward fill: carry the previous step's id within a session while the
       time gap is small (e.g. SAP screens without the invoice in the title).
    3. Backward fill: a step preceding the first identified step in a session
       (e.g. opening the mail client) inherits the upcoming id.
    4. Episode fallback (opt-in): remaining steps are grouped into contiguous
       bursts separated by more than ``max_gap`` and each burst becomes a case.

    Off-screen steps (human-reported phone/paper/meeting time) never inherit a
    neighbour's id: only an annotation can attach them to a case.
    """
    for s in steps:
        if s.case_source == Source.HUMAN:
            continue
        cid = extract_case_id(f"{s.window_title} {s.url}", patterns)
        s.case_id = cid
        s.case_source = Source.OBSERVED if cid else None

    by_session: dict[str, list[Step]] = {}
    for s in steps:
        by_session.setdefault(s.session_id, []).append(s)

    def fillable(s: Step) -> bool:
        return not s.case_id and s.app != OFF_SCREEN_APP

    for seq in by_session.values():
        seq.sort(key=lambda s: s.start)
        prev: Step | None = None
        for s in seq:
            if fillable(s) and prev and prev.case_id and s.start - prev.end <= max_gap:
                s.case_id, s.case_source = prev.case_id, Source.FILLED
            prev = s
        nxt: Step | None = None
        for s in reversed(seq):
            if fillable(s) and nxt and nxt.case_id and nxt.start - s.end <= max_gap:
                s.case_id, s.case_source = nxt.case_id, Source.FILLED
            nxt = s

    if episodes:
        counter: dict[str, int] = {}
        for seq in by_session.values():
            prev = None
            for s in seq:
                if s.case_id:
                    prev = None
                    continue
                if prev is None or s.start - prev.end > max_gap:
                    counter[s.user] = counter.get(s.user, 0) + 1
                s.case_id = f"EP-{s.user}-{counter[s.user]}"
                s.case_source = Source.EPISODE
                prev = s
    return steps
