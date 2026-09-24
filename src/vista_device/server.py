"""The sidecar protocol: one JSON object per line on stdin, one per line on stdout.

Request  `{"id": 7, "method": "observe", "params": {...}}`
Response `{"id": 7, "result": {...}}` or `{"id": 7, "error": {"code": "...", "message": "..."}}`

Methods
- `health`               → `{ok, version, drivers: {browser: bool, desktop: bool}, browser_use: bool}`
- `open`                 params `{kind, allowed_domains?, user_data_dir?, headless?, screenshots?, vocabulary?}`
- `observe`              params `{kind}` → `{observation, cloud, leakage}`
- `perform`              params `{kind, step}` → the harness result shape
                          (`ok, description, observation, result, evidence, error`) + `cloud`, `leakage`
- `frame`                params `{kind, url, title, candidates, landmarks, dialog?, text?, sensitive?, vocabulary?}`
                          → same as `observe`, computed from candidates the caller already has (the
                          recorder's own AX walk / the old CDP path) — no driver needed
- `close`                params `{kind?}`

Every response that carries a `cloud` block has been through the leakage test against the
context the caller registered with `context` (`{values, titles}`); `leakage.ok == false` means
the caller must not upload it. The sidecar never opens a network connection of its own.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from secrets import token_hex

from taskmining.leakage import RecordingContext, check
from taskmining.normalise import Vocabulary
from vista_device.drivers import Driver, DriverError, Result
from vista_device.drivers.browser import BrowserDriver, BrowserSession
from vista_device.drivers.desktop_macos import DesktopDriver
from vista_device.drivers.desktop_macos import available as desktop_available
from vista_device.frame import Candidate, Frame, frame_from_candidates

VERSION = "0.1.0"
DriverFactory = Callable[[dict], Driver]


def _observation_id() -> str:
    return f"obs-{token_hex(6)}"


@dataclass
class Sidecar:
    factories: dict[str, DriverFactory] = field(default_factory=dict)
    drivers: dict[str, Driver] = field(default_factory=dict)
    context: RecordingContext = field(default_factory=lambda: RecordingContext.build())
    vocab: Vocabulary | None = None

    # ---- methods ------------------------------------------------------------------------------

    async def health(self, params: dict) -> dict:
        has_browser_use = importlib.util.find_spec("browser_use") is not None
        return {
            "ok": True,
            "version": VERSION,
            "drivers": {k: k in self.factories for k in ("browser", "desktop")},
            "browser_use": has_browser_use,
        }

    async def set_context(self, params: dict) -> dict:
        vocab = Vocabulary.from_json(params["vocabulary"]) if params.get("vocabulary") else None
        self.vocab = vocab
        self.context = RecordingContext.build(values=params.get("values", ()), titles=params.get("titles", ()), vocab=vocab)
        return {"ok": True, "values": len(self.context.values), "titles": len(self.context.titles)}

    async def open(self, params: dict) -> dict:
        kind = str(params.get("kind", "browser"))
        factory = self.factories.get(kind)
        if factory is None:
            raise DriverError("harness_unsupported", f"No {kind} driver in this sidecar.")
        if kind in self.drivers:
            await self.drivers[kind].close()
        options = dict(params)
        options["vocab"] = self.vocab
        self.drivers[kind] = factory(options)
        return {"ok": True, "capabilities": self.drivers[kind].capabilities()}

    async def observe(self, params: dict) -> dict:
        driver = self._driver(params)
        if isinstance(driver, DesktopDriver) and params.get("pid") is not None:
            driver.bind(int(params["pid"]))
        return self._package(await driver.observe())

    async def perform(self, params: dict) -> dict:
        driver = self._driver(params)
        step = dict(params.get("step") or {})
        result = await driver.perform(step, None)
        return self._result(result)

    async def frame(self, params: dict) -> dict:
        vocab = Vocabulary.from_json(params["vocabulary"]) if params.get("vocabulary") else self.vocab
        candidates = [
            Candidate(
                id=str(c.get("id", i)),
                role=str(c.get("role", "")),
                name=str(c.get("name", "")),
                kind=str(c.get("kind", "interactive")),
                landmark=str(c.get("landmark", "")),
                position=str(c.get("position", "unknown")),
                has_value=bool((c.get("attrs") or {}).get("has_value", c.get("has_value", False))),
                disabled=bool((c.get("attrs") or {}).get("disabled", c.get("disabled", False))),
                primary=bool((c.get("attrs") or {}).get("primary", c.get("primary", False))),
            )
            for i, c in enumerate(params.get("candidates") or [])
        ]
        f = frame_from_candidates(
            kind=str(params.get("kind", "browser")),
            url=str(params.get("url", "")),
            title=str(params.get("title", "")),
            candidates=candidates,
            landmarks=[str(m) for m in params.get("landmarks") or []],
            dialog=params.get("dialog"),
            text=str(params.get("text", "")),
            sensitive=bool(params.get("sensitive", False)),
            vocab=vocab,
        )
        return self._package(f)

    async def close(self, params: dict) -> dict:
        kinds = [params["kind"]] if params.get("kind") else list(self.drivers)
        for k in kinds:
            d = self.drivers.pop(k, None)
            if d is not None:
                await d.close()
        return {"ok": True}

    # ---- helpers ------------------------------------------------------------------------------

    def _driver(self, params: dict) -> Driver:
        kind = str(params.get("kind", "browser"))
        d = self.drivers.get(kind)
        if d is None:
            raise DriverError("harness_unsupported", f"The {kind} driver is not open.")
        return d

    def _package(self, f: Frame) -> dict:
        cloud = f.cloud()
        return {"observation": f.observation(_observation_id()), "cloud": cloud, "leakage": check(cloud, self.context).to_json()}

    def _result(self, r: Result) -> dict:
        out = {
            "ok": r.ok,
            "description": r.description,
            "observation": None,
            "result": r.result,
            "evidence": r.evidence,
            "error": r.error,
            "cloud": None,
            "leakage": None,
        }
        if r.frame is not None:
            out.update(self._package(r.frame))
        return out

    async def dispatch(self, request: dict) -> dict:
        rid = request.get("id")
        method = str(request.get("method", ""))
        params = dict(request.get("params") or {})
        handler: Callable[[dict], Awaitable[dict]] | None = {
            "health": self.health,
            "context": self.set_context,
            "open": self.open,
            "observe": self.observe,
            "perform": self.perform,
            "frame": self.frame,
            "close": self.close,
        }.get(method)
        if handler is None:
            return {"id": rid, "error": {"code": "unknown_method", "message": f"No method {method!r}."}}
        try:
            return {"id": rid, "result": await handler(params)}
        except DriverError as e:
            return {"id": rid, "error": {"code": e.code, "message": str(e)}}
        except Exception as e:  # noqa: BLE001 - one bad request must not take the sidecar down
            return {"id": rid, "error": {"code": "sidecar_error", "message": str(e)[:500]}}


def default_factories() -> dict[str, DriverFactory]:
    factories: dict[str, DriverFactory] = {}
    if BrowserSession is not None:
        factories["browser"] = lambda o: BrowserDriver(
            allowed_domains=o.get("allowed_domains"),
            user_data_dir=o.get("user_data_dir"),
            headless=bool(o.get("headless", False)),
            vocab=o.get("vocab"),
            screenshots=bool(o.get("screenshots", False)),
        )
    if desktop_available():
        factories["desktop"] = lambda o: DesktopDriver(vocab=o.get("vocab"))
    return factories


async def serve(sidecar: Sidecar, reader=None, writer=None) -> None:
    """Run the NDJSON loop until stdin closes. `reader`/`writer` are injectable for tests."""
    loop = asyncio.get_running_loop()
    if reader is None:
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    out = writer or sys.stdout
    while True:
        line = await reader.readline()
        if not line:
            break
        text = line.decode("utf-8", "replace").strip() if isinstance(line, bytes) else line.strip()
        if not text:
            continue
        try:
            request = json.loads(text)
        except json.JSONDecodeError:
            response = {"id": None, "error": {"code": "bad_request", "message": "Not a JSON object."}}
        else:
            response = await sidecar.dispatch(request)
        out.write(json.dumps(response, separators=(",", ":")) + "\n")
        out.flush()
    await sidecar.close({})


def main() -> None:
    asyncio.run(serve(Sidecar(factories=default_factories())))
