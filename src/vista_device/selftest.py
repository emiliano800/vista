"""The recorded device self-test: a harness is advertised to the run loop only after it has
passed on *this* computer, with the record on disk (`~/.vista/device/selftest-<kind>.json`).

    python -m vista_device selftest desktop [--app TextEdit]
    python -m vista_device selftest browser

Passing means: `open` succeeded, `observe()` returned a settled frame with a non-empty L0 and at
least one candidate, `extract` read something, the cloud package passed the leakage check.
Nothing from the frame is written to the record except sizes and the L0 tokens (already
cloud-safe by construction); the text excerpt stays in memory.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from vista_device.selftest_record import DEFAULT_STORE, KINDS, SelfTest, passed, record_path
from vista_device.server import Sidecar, default_factories

__all__ = ["DEFAULT_STORE", "KINDS", "SelfTest", "main", "passed", "record_path", "run", "run_and_record"]


async def run(sidecar: Sidecar, kind: str, options: dict | None = None) -> SelfTest:
    if kind not in KINDS:
        return SelfTest(kind=kind, ok=False, error={"code": "invalid_value", "message": f"Unknown harness {kind!r}."})
    try:
        await sidecar.open({"kind": kind, **(options or {})})
        observed = await sidecar.observe({"kind": kind})
        obs = observed["observation"]
        read = await sidecar.perform({"kind": kind, "step": {"action": "extract"}})
        text = str((read.get("result") or {}).get("text") or "")
        leakage_ok = bool(observed["leakage"]["ok"]) and (read.get("leakage") is None or bool(read["leakage"]["ok"]))
        test = SelfTest(
            kind=kind,
            ok=bool(obs["l0"]) and bool(obs["candidates"]) and bool(obs.get("settled", True)) and read["ok"] and leakage_ok,
            l0=sorted(obs["l0"]),
            candidates=len(obs["candidates"]),
            settled=bool(obs.get("settled", True)),
            text_chars=len(text),
            leakage_ok=leakage_ok,
        )
        if not test.ok:
            test.error = {"code": "self_test_failed", "message": "The frame was empty, unsettled, unreadable or leaked."}
        return test
    except Exception as e:  # noqa: BLE001 - the record is the point; never crash out of a self-test
        code = e.code if hasattr(e, "code") and isinstance(e.code, str) else "harness_error"
        return SelfTest(kind=kind, ok=False, error={"code": code, "message": str(e)[:500]})
    finally:
        await sidecar.close({"kind": kind})


async def run_and_record(kind: str, options: dict | None = None, store: Path = DEFAULT_STORE) -> SelfTest:
    sidecar = Sidecar(factories=default_factories())
    test = await run(sidecar, kind, options)
    store.mkdir(parents=True, exist_ok=True)
    record_path(kind, store).write_text(json.dumps(test.to_json(), indent=2) + "\n")
    return test


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in KINDS:
        print(f"usage: python -m vista_device selftest {{{'|'.join(KINDS)}}} [--app NAME]", file=sys.stderr)
        return 2
    kind = argv[0]
    options: dict = {}
    if "--app" in argv:
        options["app"] = argv[argv.index("--app") + 1]
    test = asyncio.run(run_and_record(kind, options))
    print(json.dumps(test.to_json(), indent=2))
    return 0 if test.ok else 1
