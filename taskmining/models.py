from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import TextIO


class EventType(str, Enum):
    CLICK = "click"
    KEY = "key"
    FOCUS = "focus"
    COPY = "copy"
    PASTE = "paste"
    SCROLL = "scroll"


@dataclass
class RawEvent:
    """A single low-level desktop interaction as recorded by the client."""

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
    n_keys: int = 0
    case_id: str = ""

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
