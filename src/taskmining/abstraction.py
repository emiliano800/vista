"""Activity abstraction: lift raw UI events to business-level steps.

Celonis calls these "activity definitions": rules matching on application,
window title and URL that map to a human-readable activity name. Consecutive
events matching the same activity are merged into one ``Step``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from taskmining.models import EventType, RawEvent, Source, Step


@dataclass(frozen=True)
class ActivityRule:
    activity: str
    app: str | None = None
    title: str | None = None
    url: str | None = None
    element: str | None = None
    event_type: EventType | None = None

    def matches(self, e: RawEvent) -> bool:
        if self.app and not re.search(self.app, e.app, re.I):
            return False
        if self.title and not re.search(self.title, e.window_title, re.I):
            return False
        if self.url and not re.search(self.url, e.url, re.I):
            return False
        if self.element and not re.search(self.element, e.element, re.I):
            return False
        if self.event_type and e.event_type != self.event_type:
            return False
        return True


DEFAULT_RULES: list[ActivityRule] = [
    ActivityRule("Send Reply Email", app="Outlook", element="^Send$", event_type=EventType.CLICK),
    ActivityRule("Write Reply Email", app="Outlook", title=r"^RE:"),
    ActivityRule("Read Invoice Email", app="Outlook", title=r"Invoice"),
    ActivityRule("Read Email", app="Outlook"),
    ActivityRule("Review Invoice PDF", app="Acrobat"),
    ActivityRule("Lookup Vendor (SAP XK03)", app="SAP", title="Display Vendor"),
    ActivityRule("Post Invoice (SAP MIRO)", app="SAP", title="Incoming Invoice", element="^Post$", event_type=EventType.CLICK),
    ActivityRule("Enter Invoice (SAP MIRO)", app="SAP", title="Incoming Invoice"),
    ActivityRule("Update AP Tracker (Excel)", app="Excel", title="AP_Tracker"),
    ActivityRule("Work in Excel", app="Excel"),
]


def classify(e: RawEvent, rules: list[ActivityRule]) -> tuple[str, Source]:
    for r in rules:
        if r.matches(e):
            return r.activity, Source.RULE
    return f"Other ({e.app})", Source.FALLBACK


def is_transfer(e: RawEvent) -> bool:
    """A paste whose clipboard content the recorder saw copied in another app."""
    src = e.payload.get("source_app")
    return e.event_type == EventType.PASTE and bool(src) and src != e.app


def _bump(step: Step, e: RawEvent) -> None:
    step.end = max(step.end, e.timestamp)
    step.n_events += 1
    if e.event_type == EventType.COPY:
        step.n_copies += 1
    elif e.event_type == EventType.PASTE:
        step.n_pastes += 1
        if is_transfer(e):
            step.n_transfers += 1
    elif e.event_type == EventType.KEY:
        step.n_keys += int(e.payload.get("n_keys", 1))


def abstract(sessioned: Iterable[tuple[str, RawEvent]], rules: list[ActivityRule] | None = None) -> list[Step]:
    """Turn (session_id, event) pairs into merged Steps."""
    rules = rules if rules is not None else DEFAULT_RULES
    steps: list[Step] = []
    for sid, e in sessioned:
        act, src = classify(e, rules)
        cur = steps[-1] if steps else None
        if cur and cur.session_id == sid and cur.activity == act:
            _bump(cur, e)
            continue
        s = Step(
            start=e.timestamp,
            end=e.timestamp,
            user=e.user,
            session_id=sid,
            app=e.app,
            activity=act,
            window_title=e.window_title,
            url=e.url,
            n_events=0,
            activity_source=src,
        )
        _bump(s, e)
        steps.append(s)
    return steps
