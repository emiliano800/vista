"""Score agent output against synthetic_data/answer_key.json.

A prediction matches an item when kind, at least one company and at least one
evidence file agree. Matching a trap item is a false positive. Only this module
reads the answer key."""

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from vista.agents.synthetic import AnswerItem


@dataclass(frozen=True)
class Prediction:
    kind: str
    companies: frozenset[str]  # short names
    evidence: frozenset[str]  # table refs, e.g. '11_billing_ar/customer_invoices.csv' or '.../x.xlsx#items'
    title: str = ""
    text: str = ""  # anything else the prediction says (shared key, detail); used to tell look-alike items apart

    @classmethod
    def from_finding(cls, company_short: str, row: dict) -> "Prediction":
        refs = row.get("evidence_refs") or [row.get("source_ref", {})]
        files = {r["file"] if isinstance(r, dict) else str(r) for r in refs}
        kind = row.get("kind") or (row.get("args") or {}).get("kind") or "data_quality"
        title = row.get("title") or (row.get("args") or {}).get("title") or ""
        detail = row.get("detail") or (row.get("args") or {}).get("detail") or ""
        return cls(kind=kind, companies=frozenset({company_short}), evidence=frozenset(files), title=title, text=str(detail))

    @classmethod
    def from_opportunity(cls, row: dict) -> "Prediction":
        files = {e.split(":", 1)[-1] for e in row.get("evidence", [])}
        return cls(
            kind=row["kind"],
            companies=frozenset(row["companies"]),
            evidence=frozenset(files),
            title=row.get("title", ""),
            text=f"{row.get('shared_key', '')} {row.get('detail', '')}",
        )


@dataclass
class Score:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    trap_hits: int = 0
    matched: list[tuple[str, str]] = field(default_factory=list)  # (item id, prediction title)
    missed: list[str] = field(default_factory=list)  # item ids
    unmatched: list[str] = field(default_factory=list)  # prediction titles

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    def as_dict(self) -> dict:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "trap_hits": self.trap_hits,
            "precision": round(self.precision, 3),
            "recall": round(self.recall, 3),
            "matched": self.matched,
            "missed": self.missed,
            "unmatched": self.unmatched,
        }


def _norm(ref: str) -> str:
    """'*/11_finance_gl/x.csv' -> '11_finance_gl/x.csv'; keeps '#sheet'; compares by division/file."""
    path, _, sheet = ref.partition("#")
    parts = PurePosixPath(path).parts
    parts = tuple(p for p in parts if p != "*")
    base = "/".join(parts[-2:]) if len(parts) >= 2 else path
    return f"{base}#{sheet}" if sheet else base


def evidence_overlaps(pred: frozenset[str], item: list[str]) -> bool:
    p = {_norm(e) for e in pred}
    p_files = {e.split("#")[0] for e in p}
    for e in item:
        n = _norm(e)
        if n in p or n.split("#")[0] in p_files:
            return True
    return False


def matches(pred: Prediction, item: AnswerItem) -> bool:
    return pred.kind == item.kind and bool(pred.companies & set(item.companies)) and evidence_overlaps(pred.evidence, item.evidence)


_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-\.]{2,}")
_STOP = {
    "the",
    "and",
    "not",
    "same",
    "are",
    "for",
    "with",
    "from",
    "than",
    "pays",
    "more",
    "less",
    "price",
    "every",
    "company",
    "companies",
    "vs",
    "both",
    "but",
    "business",
    "using",
    "used",
    "under",
    "different",
    "names",
    "multiple",
    "products",
    "serving",
    "function",
    "tools",
    "billed",
    "overlapping",
    "portfolio",
    "separate",
    "across",
    "item",
}


def _tokens(text: str) -> set[str]:
    return {t.lower().strip(".") for t in _TOKEN.findall(text)} - _STOP


def disambiguate(pred: Prediction, candidates: list[AnswerItem]) -> list[AnswerItem]:
    """Several items can share kind/companies/evidence (e.g. every purchasing_price_gap). Keep the ones
    whose title shares specific tokens (part numbers, vendor names) with the prediction; if none do,
    keep a single candidate as-is, otherwise nothing (ambiguous)."""
    if len(candidates) <= 1:
        return candidates
    words = _tokens(f"{pred.title} {pred.text}")
    scored = [(len(_tokens(i.title) & words), i) for i in candidates]
    best = max(s for s, _ in scored)
    if not best:
        return []
    top = [i for s, i in scored if s == best]
    # A trap only counts when it is the unambiguous best match; a tie with a real item is the real item.
    real = [i for i in top if not i.is_false_positive_trap]
    return real or top


def score(
    predictions: list[Prediction], items: list[AnswerItem], companies: set[str] | None = None, kinds: set[str] | None = None
) -> Score:
    """Restrict items to the companies/kinds the agent was asked about, so recall is fair."""
    scope = [i for i in items if (companies is None or set(i.companies) & companies) and (kinds is None or i.kind in kinds)]
    s = Score()
    hit: set[str] = set()
    for pred in predictions:
        found = disambiguate(pred, [i for i in scope if matches(pred, i)])
        if not found:
            s.fp += 1
            s.unmatched.append(pred.title)
            continue
        for item in found:
            if item.is_false_positive_trap:
                s.trap_hits += 1
                s.fp += 1
            elif item.id not in hit:
                s.tp += 1
                hit.add(item.id)
            s.matched.append((item.id, pred.title))
    for item in scope:
        if not item.is_false_positive_trap and item.id not in hit:
            s.fn += 1
            s.missed.append(item.id)
    return s
