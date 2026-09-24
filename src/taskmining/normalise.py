"""The one normaliser for everything cloud-bound (design of record: docs/computer_use_system.md §2, §4).

Every control name, activity label, descriptor or slot value that may leave the device passes
through `normalise()` before anything else sees it. Two rules, in this order:

1. Data-shaped runs become class tokens: emails, IBANs, card numbers, phone numbers, dates,
   amounts, identifiers (letters with digits, e.g. `INV-1042`) and bare digit runs. The value
   never survives, only its class.
2. Any remaining word not in the app's *control vocabulary* becomes `{text}`. The vocabulary is
   the set of accessibility names that recur across at least `MIN_SCREENS` records/screens —
   words that name the app's controls rather than the data shown in them. A word seen on one
   screen only is data until proven otherwise.

Rows are named from their headers and position (`row_name`), never from cell text. Everything
here is deterministic and free of any model, so the leakage check (`taskmining.leakage`) can
enumerate exactly the tokens the normaliser produces and fail on anything else.

Known limits, reported rather than hidden: a single lower-case surname that happens to recur
across screens joins the vocabulary; non-Latin scripts are treated as words and pass through
rule 2 only (no class detection). See `KNOWN_LIMITS`.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

MIN_SCREENS = 2
MAX_LEN = 255

# Ordered: the more specific shapes first so a card number is `{card}`, not five `{number}`s.
CLASS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?\b")),
    ("card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    (
        "date",
        re.compile(
            r"\b(?:\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?"
            r"|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}"
            r"|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+\d{2,4}"
            r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{2,4})\b",
            re.I,
        ),
    ),
    ("phone", re.compile(r"(?<![\w.])\+?\(?\d[\d\s().-]{7,}\d\b")),
    ("amount", re.compile(r"(?:[$€£¥]\s?-?\d[\d,]*(?:\.\d+)?|-?\d[\d,]*\.\d{2}\b|\b\d[\d,]*\s?(?:USD|EUR|GBP|CHF)\b)")),
    ("id", re.compile(r"\b(?=[A-Za-z0-9#_-]*\d)(?=[A-Za-z0-9#_-]*[A-Za-z#])[A-Za-z0-9#_-]{3,}\b")),
    ("number", re.compile(r"\d+")),
)
TOKENS: frozenset[str] = frozenset({f"{{{name}}}" for name, _ in CLASS_PATTERNS} | {"{text}"})
_TOKEN = re.compile(r"\{[a-z]+\}")
_WORD = re.compile(r"\{[a-z]+\}|[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)
_SPACE = re.compile(r"\s+")

KNOWN_LIMITS: tuple[str, ...] = (
    "A single lower-case surname that recurs across screens is indistinguishable from a control name and joins the vocabulary.",
    "Non-Latin scripts get no class detection; they pass through the vocabulary rule only.",
    "Letters-only identifiers (a customer code such as `ACME`) are words to the normaliser; they leave only via the vocabulary.",
)


def classify(text: str) -> str:
    """Rule 1 only: replace data-shaped runs with class tokens. Case and spacing untouched."""
    out = str(text or "")
    for name, pattern in CLASS_PATTERNS:
        out = pattern.sub(f"{{{name}}}", out)
    return out


def words(text: str) -> list[str]:
    """The lower-cased words of `text` (class tokens count as one word)."""
    return [w.lower() for w in _WORD.findall(str(text or ""))]


@dataclass(frozen=True)
class Vocabulary:
    """Words allowed to leave the device as themselves: control names that recur across screens.

    Built from the accessibility names of each screen (or record) of one app. A word is in the
    vocabulary when it appears on at least `min_screens` distinct screens after `classify()` —
    the label "Amount" recurs on every bill, the value in it does not. `extra` is for names
    declared by a person (storyboard renames, declared input names); they join without a count.
    """

    words: frozenset[str] = frozenset()
    min_screens: int = MIN_SCREENS
    screens: int = 0
    extra: frozenset[str] = frozenset()
    method: str = field(default="recurring-ax-names/1")

    @classmethod
    def build(cls, screens: Iterable[Iterable[str]], *, min_screens: int = MIN_SCREENS, extra: Iterable[str] = ()) -> Vocabulary:
        seen: Counter[str] = Counter()
        n = 0
        for names in screens:
            n += 1
            per_screen: set[str] = set()
            for name in names:
                per_screen.update(w for w in words(classify(name)) if not _TOKEN.fullmatch(w))
            seen.update(per_screen)
        extra_words = frozenset(w for e in extra for w in words(classify(e)) if not _TOKEN.fullmatch(w))
        vocab = frozenset(w for w, c in seen.items() if c >= min_screens)
        return cls(words=vocab, min_screens=min_screens, screens=n, extra=extra_words)

    def __contains__(self, word: str) -> bool:
        w = word.lower()
        return w in self.words or w in self.extra

    def to_json(self) -> dict:
        return {
            "method": self.method,
            "min_screens": self.min_screens,
            "screens": self.screens,
            "size": len(self.words) + len(self.extra),
            "words": sorted(self.words),
            "extra": sorted(self.extra),
        }


def normalise(text: str, vocab: Vocabulary | None = None) -> str:
    """Both rules. Lower-case, single-spaced, at most `MAX_LEN` characters. With no vocabulary
    every word is data: the result is class tokens and `{text}` only."""
    parts: list[str] = []
    for w in words(classify(text)):
        if _TOKEN.fullmatch(w):
            parts.append(w)
        elif vocab is not None and w in vocab:
            parts.append(w)
        elif parts and parts[-1] == "{text}":
            continue
        else:
            parts.append("{text}")
    return _SPACE.sub(" ", " ".join(parts)).strip()[:MAX_LEN]


def row_name(headers: Iterable[str], position: int, vocab: Vocabulary | None = None) -> str:
    """A table row is named from its column headers and its position, never from its cells."""
    heads = [normalise(h, vocab) for h in headers]
    heads = [h for h in heads if h and h != "{text}"][:4]
    return f"row {position} of ({', '.join(heads) or 'table'})"
