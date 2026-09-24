"""Leakage test: does a cloud-bound payload carry anything the recording knows?

Run at compile time over the whole graph and on every cloud-bound observation. A payload
*fails* when any string in it

- contains a value the recording saw (typed text, clipboard, cell text, file names) — `recorded_value`;
- contains a window title — `window_title`;
- carries a `{token}` the normaliser does not produce — `unknown_token`;
- names a control with a word outside the app's vocabulary — `non_vocab_name` (checked on the
  keys that name things: `NAME_KEYS`; hashes and enumerations are exempt).

The report never repeats the offending value: it gives the JSON path, the reason and the length
of the match, which is enough to find and fix the producer. `ocr_survivors()` is the device-side
hook: a sample of OCR'd screen text is searched for recorded values that a redaction step should
have removed. Known limits of the normaliser are copied onto every report so they are visible
where the result is read, not buried in a module docstring.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from taskmining.normalise import KNOWN_LIMITS, TOKENS, Vocabulary, words
from taskmining.state import APP_ROLES

MIN_VALUE_LEN = 3
# Keys whose string values name a control or a screen; only these face the vocabulary rule.
NAME_KEYS: frozenset[str] = frozenset({"name", "control", "activity", "label", "title", "descriptor", "aliases", "landmark"})
_TOKEN = re.compile(r"\{[a-z_]+\}")
_HASH = re.compile(r"^[0-9a-f]{8,64}$")
_SLOT_PREFIX = re.compile(r"^(?:doc|rec|field|fact|dialog|have|read|open|in|ctx):")


@dataclass(frozen=True)
class Failure:
    path: str
    reason: str
    length: int = 0

    def to_json(self) -> dict:
        return {"path": self.path, "reason": self.reason, "length": self.length}


@dataclass
class Report:
    ok: bool
    failures: list[Failure] = field(default_factory=list)
    strings_checked: int = 0
    known_limits: tuple[str, ...] = KNOWN_LIMITS

    def to_json(self) -> dict:
        return {
            "ok": self.ok,
            "strings_checked": self.strings_checked,
            "failures": [f.to_json() for f in self.failures],
            "known_limits": list(self.known_limits),
        }


@dataclass(frozen=True)
class RecordingContext:
    """What the recording knows and must not leak. All comparisons are case-insensitive; values
    shorter than `MIN_VALUE_LEN` are ignored (a lone digit or `ok` is not a leak)."""

    values: frozenset[str]
    titles: frozenset[str]
    vocab: Vocabulary | None = None

    @classmethod
    def build(cls, *, values: Iterable[str] = (), titles: Iterable[str] = (), vocab: Vocabulary | None = None) -> RecordingContext:
        def keep(s: str) -> bool:
            return bool(s) and len(s.strip()) >= MIN_VALUE_LEN

        return cls(
            values=frozenset(v.strip().lower() for v in values if keep(v)),
            titles=frozenset(t.strip().lower() for t in titles if keep(t)),
            vocab=vocab,
        )


def _strings(payload: object, path: str = "$") -> Iterator[tuple[str, str, str]]:
    """Every string in `payload` as (path, last key, value)."""
    if isinstance(payload, str):
        yield path, path.rsplit(".", 1)[-1].split("[", 1)[0], payload
    elif isinstance(payload, dict):
        for k, v in payload.items():
            yield from _strings(v, f"{path}.{k}")
    elif isinstance(payload, list | tuple):
        for i, v in enumerate(payload):
            yield from _strings(v, f"{path}[{i}]")


def _exempt(value: str) -> bool:
    return bool(_HASH.match(value) or _SLOT_PREFIX.match(value) or value in APP_ROLES)


def check(payload: object, ctx: RecordingContext) -> Report:
    failures: list[Failure] = []
    n = 0
    for path, key, value in _strings(payload):
        n += 1
        low = value.lower()
        for v in ctx.values:
            if v in low:
                failures.append(Failure(path, "recorded_value", len(v)))
                break
        for t in ctx.titles:
            if t in low:
                failures.append(Failure(path, "window_title", len(t)))
                break
        for tok in _TOKEN.findall(value):
            if tok not in TOKENS:
                failures.append(Failure(path, "unknown_token", len(tok)))
        if key in NAME_KEYS and ctx.vocab is not None and not _exempt(value):
            bad = [w for w in words(value) if not _TOKEN.fullmatch(w) and w not in ctx.vocab]
            if bad:
                failures.append(Failure(path, "non_vocab_name", len(bad)))
    return Report(ok=not failures, failures=failures, strings_checked=n)


def ocr_survivors(sample: str, ctx: RecordingContext) -> Report:
    """Device-side: recorded values still legible in an OCR sample of what is about to leave."""
    low = (sample or "").lower()
    hits = [Failure("ocr", "recorded_value", len(v)) for v in sorted(ctx.values) if v in low]
    hits += [Failure("ocr", "window_title", len(t)) for t in sorted(ctx.titles) if t in low]
    return Report(ok=not hits, failures=hits, strings_checked=1)
