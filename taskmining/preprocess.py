"""Preprocessing: PII redaction, keystroke aggregation, sessionization."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import replace
from datetime import timedelta

from taskmining.models import EventType, RawEvent

PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "EMAIL": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "IBAN": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
    "CARD": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    "PHONE": re.compile(r"\+?\d[\d\s().-]{7,}\d"),
}


def redact_text(text: str) -> str:
    for label, pat in PII_PATTERNS.items():
        text = pat.sub(f"<{label}>", text)
    return text


def redact(events: Iterable[RawEvent]) -> list[RawEvent]:
    """Strip PII from free-text fields. Single keystrokes are kept (needed to
    build bursts) but the aggregated burst text is redacted afterwards."""
    out = []
    for e in events:
        if e.event_type == EventType.KEY:
            out.append(e)
        else:
            out.append(replace(e, text=redact_text(e.text), window_title=redact_text(e.window_title)))
    return out


def pseudonymize_users(events: Iterable[RawEvent], salt: str = "vista") -> list[RawEvent]:
    def h(u: str) -> str:
        return "user_" + hashlib.sha256(f"{salt}:{u}".encode()).hexdigest()[:8]

    return [replace(e, user=h(e.user)) for e in events]


def aggregate_keystrokes(events: Iterable[RawEvent], gap: timedelta = timedelta(seconds=2)) -> list[RawEvent]:
    """Collapse runs of KEY events in the same window into one typing burst.

    The burst carries ``payload["n_keys"]`` and a redacted concatenation of
    the typed text.
    """
    out: list[RawEvent] = []
    burst: list[RawEvent] = []

    def flush():
        if not burst:
            return
        first = burst[0]
        text = redact_text("".join(b.text for b in burst))
        out.append(
            replace(
                first,
                text=text,
                payload={**first.payload, "n_keys": len(burst), "end": burst[-1].timestamp.isoformat()},
            )
        )
        burst.clear()

    for e in events:
        if e.event_type == EventType.KEY:
            if burst and (e.window_title != burst[-1].window_title or e.user != burst[-1].user or e.timestamp - burst[-1].timestamp > gap):
                flush()
            burst.append(e)
        else:
            flush()
            out.append(e)
    flush()
    return out


def sessionize(events: Iterable[RawEvent], idle: timedelta = timedelta(minutes=10)) -> list[tuple[str, RawEvent]]:
    """Assign a session id per user; a new session starts after ``idle``."""
    last: dict[str, RawEvent] = {}
    counter: dict[str, int] = {}
    out = []
    for e in sorted(events, key=lambda x: (x.timestamp, x.user)):
        prev = last.get(e.user)
        if prev is None or e.timestamp - prev.timestamp > idle:
            counter[e.user] = counter.get(e.user, 0) + 1
        last[e.user] = e
        out.append((f"{e.user}#{counter[e.user]}", e))
    return out
