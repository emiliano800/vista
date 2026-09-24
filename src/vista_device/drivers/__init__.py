"""Driver adapters over the open-source observation/action layers.

A driver exposes exactly two things to the sidecar: `observe() -> Frame` and
`perform(step, frame) -> Result`. Both libraries' agents, prompts and model calls are never
imported; Jev (through the run loop) is the only policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from vista_device.frame import Frame

ACTIONS: tuple[str, ...] = ("observe", "navigate", "click", "type", "press", "extract", "screenshot", "wait")


@dataclass
class Result:
    ok: bool
    description: str
    frame: Frame | None = None
    result: dict | None = None
    evidence: dict | None = None
    error: dict | None = field(default=None)

    @classmethod
    def refused(cls, action: str, code: str, message: str) -> Result:
        return cls(ok=False, description=f"{action} refused: {message}", error={"code": code, "message": message})


class DriverError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Driver(Protocol):
    kind: str

    def capabilities(self) -> list[str]: ...

    async def observe(self) -> Frame: ...

    async def perform(self, step: dict, frame: Frame | None) -> Result: ...

    async def close(self) -> None: ...
