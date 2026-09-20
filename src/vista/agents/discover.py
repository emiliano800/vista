"""Discover phase — file_reviewer: one table in, observed facts out.

Code does the counting (blank ratio, mixed date formats, money strings,
truncated headers, min>max inversions); the model interprets the profile and
names what the columns really hold. Facts carry file + column references."""

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field

from pydantic import BaseModel, Field, ValidationError

from vista.agents.llm import Prompt, strip_fences
from vista.agents.synthetic import Company, Table

SAMPLE_ROWS = 5
DATE_FORMATS = {
    "iso": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    "us_slash": re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$"),
    "dmy_text": re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{4}$"),
    "dotted": re.compile(r"^\d{4}\.\d{2}\.\d{2}$"),
    "mdy_text": re.compile(r"^[A-Za-z]{3} \d{1,2}, \d{4}$"),
}
MONEY = re.compile(r"^\$\s?-?[\d,]+(\.\d+)?$")
NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


@dataclass
class ColumnProfile:
    name: str
    blank_ratio: float
    distinct_ratio: float
    formats: dict[str, int] = field(default_factory=dict)  # iso|us_slash|...|money_string|number
    flags: list[str] = field(default_factory=list)  # truncated_header|mixed_date_formats|money_as_text|empty


@dataclass
class TableProfile:
    ref: str
    row_count: int
    header_style: str  # snake|title|upper|mixed
    columns: list[ColumnProfile]
    flags: list[str]  # table-level: min_gt_max:<min>/<max>, duplicate_rows:<n>
    sample: list[dict]


def _header_style(cols: list[str]) -> str:
    if not cols:
        return "mixed"
    if all(c == c.upper() for c in cols):
        return "upper"
    if all(c == c.lower() for c in cols):
        return "snake"
    if all(c[:1].isupper() for c in cols if c):
        return "title"
    return "mixed"


def _formats(values: list) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for v in values:
        s = str(v).strip()
        for name, rx in DATE_FORMATS.items():
            if rx.match(s):
                counts[name] += 1
                break
        else:
            if MONEY.match(s):
                counts["money_string"] += 1
            elif NUMBER.match(s):
                counts["number"] += 1
    return dict(counts)


def _to_number(v) -> float | None:
    if v is None:
        return None
    s = str(v).replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def profile_table(table: Table) -> TableProfile:
    n = len(table.rows)
    cols: list[ColumnProfile] = []
    for name in table.columns:
        values = table.column(name)
        present = [v for v in values if v not in (None, "")]
        fmts = _formats(present)
        flags = []
        if name.endswith("_") or name.endswith("-"):
            flags.append("truncated_header")
        if len([f for f in fmts if f in DATE_FORMATS]) > 1:
            flags.append("mixed_date_formats")
        if fmts.get("money_string") and fmts.get("number"):
            flags.append("money_as_text")
        if n and not present:
            flags.append("empty")
        cols.append(
            ColumnProfile(
                name=name,
                blank_ratio=round(1 - len(present) / n, 3) if n else 0.0,
                distinct_ratio=round(len({str(v) for v in present}) / len(present), 3) if present else 0.0,
                formats=fmts,
                flags=flags,
            )
        )

    table_flags: list[str] = []
    lower = {c.lower(): c for c in table.columns}
    mins = [c for k, c in lower.items() if "min" in k]
    maxs = [c for k, c in lower.items() if k == "max" or k.endswith("_max") or k.startswith("max")]
    for lo in mins:
        for hi in maxs:
            pairs = [(_to_number(r.get(lo)), _to_number(r.get(hi))) for r in table.rows]
            pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
            if pairs and sum(a > b for a, b in pairs) / len(pairs) > 0.5:
                table_flags.append(f"min_gt_max:{lo}/{hi}")
    seen = Counter(tuple(str(r.get(c)) for c in table.columns) for r in table.rows)
    dupes = sum(c - 1 for c in seen.values() if c > 1)
    if dupes:
        table_flags.append(f"duplicate_rows:{dupes}")

    return TableProfile(
        ref=table.ref,
        row_count=n,
        header_style=_header_style(table.columns),
        columns=cols,
        flags=table_flags,
        sample=[{k: ("" if v is None else str(v)) for k, v in r.items()} for r in table.rows[:SAMPLE_ROWS]],
    )


class Fact(BaseModel):
    subject: str = Field(max_length=256)  # table ref or column name
    predicate: str = Field(max_length=128)  # e.g. "holds", "is_duplicate_of", "format", "missing"
    value: str = Field(max_length=1024)
    confidence: float = Field(ge=0, le=1)
    columns: list[str] = Field(default_factory=list, max_length=20)


class DiscoverOutput(BaseModel):
    facts: list[Fact] = Field(default_factory=list, max_length=40)


SYSTEM = (
    "You are Vista's File Reviewer. You are given a profile of one exported table from a small company's "
    "system of record (headers, blank/distinct ratios, detected value formats, sample rows) plus code-computed "
    "flags. Report only what the evidence supports about what each column actually holds and how clean it is: "
    "mislabelled or swapped columns, truncated headers, mixed date formats, money stored as text, likely "
    "duplicates, missing datasets. Never recommend actions here; that is a later phase. "
    "Calibrate words to the numbers: quote blank_ratio as a percentage and call it 'high' only above 20%, "
    "'some' between 5% and 20%, and do not mention blanks under 5% unless the column is a key. A flag the "
    "code did not raise is a hypothesis: say 'possibly' and keep confidence at or below 0.6; confidence above "
    "0.85 is for things visible in the sample rows or flags. "
    'Respond with JSON only: {"facts": [{"subject": str, "predicate": str, "value": str, '
    '"confidence": 0..1, "columns": [str]}]}. At most 12 facts, highest confidence first.'
)


def prepare(company: Company, profile: TableProfile) -> Prompt:
    user = json.dumps(
        {
            "company": {"short": company.short, "sector": company.sector, "data_quality_tier": company.tier},
            "table": asdict(profile),
        },
        default=str,
    )
    return Prompt(system=SYSTEM, user=user, max_tokens=1200)


def parse(text: str) -> DiscoverOutput:
    try:
        return DiscoverOutput.model_validate_json(strip_fences(text))
    except ValidationError:
        return DiscoverOutput()


def deterministic_facts(profile: TableProfile) -> list[dict]:
    """Facts that need no model: derived from the profile's flags."""
    out = []
    for c in profile.columns:
        for flag in c.flags:
            out.append({"subject": c.name, "predicate": flag, "value": json.dumps(c.formats), "confidence": 1.0, "columns": [c.name]})
    for flag in profile.flags:
        name, _, detail = flag.partition(":")
        out.append(
            {
                "subject": profile.ref,
                "predicate": name,
                "value": detail,
                "confidence": 1.0,
                "columns": detail.split("/") if "/" in detail else [],
            }
        )
    return out


def apply(output: DiscoverOutput, profile: TableProfile) -> list[dict]:
    """observed_facts rows: model facts + deterministic facts, each with a source_ref."""
    known = {c.name for c in profile.columns}
    rows = []
    for f in [*deterministic_facts(profile), *(fact.model_dump() for fact in output.facts)]:
        cols = [c for c in f["columns"] if c in known]
        rows.append({**f, "columns": cols, "source_ref": {"file": profile.ref, "columns": cols}})
    return rows
