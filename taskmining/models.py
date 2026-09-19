from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TextIO


class EventType(StrEnum):
    CLICK = "click"
    KEY = "key"
    FOCUS = "focus"
    COPY = "copy"
    PASTE = "paste"
    SCROLL = "scroll"
    SHORTCUT = "shortcut"  # modifier combination (Ctrl+S, Alt+Tab); text = canonical combo, never typed content
    SCREEN = "screen"  # screenshot taken on focus change / click; payload["image"] = relative file path


@dataclass
class RawEvent:
    """A single low-level desktop interaction as recorded by the client.

    Wire format is one JSON object per line (``recording.jsonl``). The desktop
    recorder in ``recorder/`` writes exactly this shape; ``payload`` carries
    type-specific extras (``button``, ``x``/``y``, ``n_keys``, ``image``,
    ``modifiers``, ``recording_id``).
    """

    timestamp: datetime
    user: str
    event_type: EventType
    app: str
    window_title: str
    url: str = ""
    element: str = ""
    text: str = ""
    payload: dict = field(default_factory=dict)

    def to_json(self) -> str:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        d["event_type"] = self.event_type.value
        return json.dumps(d, sort_keys=True)

    @classmethod
    def from_json(cls, line: str) -> RawEvent:
        d = json.loads(line)
        d["timestamp"] = datetime.fromisoformat(d["timestamp"])
        d["event_type"] = EventType(d["event_type"])
        return cls(**d)


OFF_SCREEN_APP = "(off-screen)"  # app value of steps that only exist because a human reported them


class Source(StrEnum):
    """Provenance of a derived field, so observed facts stay separable from inference."""

    OBSERVED = "observed"  # read directly from a screen (id in window title / url)
    RULE = "rule"  # produced by an ActivityRule
    FALLBACK = "fallback"  # no rule matched -> "Other (<app>)"
    FILLED = "filled"  # propagated from a neighbouring step
    EPISODE = "episode"  # synthetic case from a contiguous burst of work (no id available)
    HUMAN = "human"  # stated by an employee / analyst annotation


@dataclass
class Annotation:
    """A human statement about what happened in a time range.

    Employees or analysts attach these to hours that the recorder cannot
    explain (phone calls, paper records, meetings) or to correct labels.
    """

    user: str
    start: datetime
    end: datetime
    label: str
    note: str = ""
    case_id: str = ""
    author: str = "employee"

    def to_json(self) -> str:
        d = asdict(self)
        d["start"] = self.start.isoformat()
        d["end"] = self.end.isoformat()
        return json.dumps(d, sort_keys=True)

    @classmethod
    def from_dict(cls, d: dict) -> Annotation:
        return cls(
            user=d["user"],
            start=datetime.fromisoformat(d["start"]),
            end=datetime.fromisoformat(d["end"]),
            label=d["label"],
            note=d.get("note", "") or "",
            case_id=d.get("case_id", "") or "",
            author=d.get("author", "employee") or "employee",
        )


@dataclass
class Step:
    """A business-level activity abstracted from one or more raw events."""

    start: datetime
    end: datetime
    user: str
    session_id: str
    app: str
    activity: str
    window_title: str
    url: str = ""
    n_events: int = 1
    n_copies: int = 0
    n_pastes: int = 0
    n_transfers: int = 0  # pastes whose clipboard content was copied in a different app
    n_keys: int = 0
    case_id: str = ""
    activity_source: Source = Source.RULE
    case_source: Source | None = None
    note: str = ""

    @property
    def duration_s(self) -> float:
        return (self.end - self.start).total_seconds()


def write_jsonl(events: list[RawEvent], fp: TextIO) -> None:
    for e in events:
        fp.write(e.to_json())
        fp.write("\n")


def read_jsonl(fp: TextIO) -> Iterator[RawEvent]:
    for line in fp:
        line = line.strip()
        if line:
            yield RawEvent.from_json(line)
