"""On-disk record of the device self-test (`selftest.py` writes it, `server.health` reads it)."""

from __future__ import annotations

import json
import platform
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

VERSION = "0.1.0"
DEFAULT_STORE = Path.home() / ".vista" / "device"
KINDS: tuple[str, ...] = ("browser", "desktop")


@dataclass
class SelfTest:
    kind: str
    ok: bool
    version: str = VERSION
    platform: str = field(default_factory=lambda: f"{platform.system()} {platform.release()}")
    at: float = field(default_factory=time.time)
    l0: list[str] = field(default_factory=list)
    candidates: int = 0
    settled: bool = False
    text_chars: int = 0
    leakage_ok: bool | None = None
    error: dict | None = None

    def to_json(self) -> dict:
        return asdict(self)


def record_path(kind: str, store: Path = DEFAULT_STORE) -> Path:
    return store / f"selftest-{kind}.json"


def load(kind: str, store: Path = DEFAULT_STORE) -> SelfTest | None:
    path = record_path(kind, store)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return SelfTest(**{k: data[k] for k in SelfTest.__dataclass_fields__ if k in data})
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def passed(kind: str, store: Path = DEFAULT_STORE) -> bool:
    """Only a record from this sidecar version on this platform counts."""
    t = load(kind, store)
    return bool(t and t.ok and t.version == VERSION and t.platform == f"{platform.system()} {platform.release()}")
