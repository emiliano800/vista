"""Settle = accessibility quiescence: the screen's structure has not changed for `WINDOW_MS`,
or `CAP_MS` have passed (then the frame is returned marked `settled=False`)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

WINDOW_MS = 200
CAP_MS = 2000
POLL_MS = 50


async def settle(
    fingerprint: Callable[[], Awaitable[str]],
    *,
    window_ms: int = WINDOW_MS,
    cap_ms: int = CAP_MS,
    poll_ms: int = POLL_MS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> bool:
    """`fingerprint` is cheap and structural (a hash of roles/names, never a screenshot).
    Returns True when a quiet window was observed within the cap."""
    start = clock()
    last = await fingerprint()
    quiet_since = clock()
    while True:
        await sleep(poll_ms / 1000)
        now = clock()
        current = await fingerprint()
        if current != last:
            last = current
            quiet_since = now
        elif (now - quiet_since) * 1000 >= window_ms:
            return True
        if (now - start) * 1000 >= cap_ms:
            return False
