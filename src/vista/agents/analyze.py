"""Analyze phase — portfolio_analyst: one table type across companies in, opportunities out.

Proposal-only. The planted traps in the synthetic data are exactly the mistakes
a careless comparison makes (generic 6205 vs SKF 6205-2RS1; Apex Fastening
Systems vs Apex Fastener Corp; two products billed by one vendor), so apply()
requires an exact shared key and at least two companies per opportunity."""

import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from vista.agents.llm import Prompt, strip_fences
from vista.agents.synthetic import Company, Table

OpportunityKind = Literal[
    "purchasing_price_gap",
    "vendor_consolidation",
    "software_overlap",
    "freight_rate_gap",
    "carrier_consolidation",
    "cross_sell",
]
MAX_ROWS_PER_TABLE = 80


class Opportunity(BaseModel):
    kind: OpportunityKind
    title: str = Field(max_length=256)
    companies: list[str] = Field(min_length=1, max_length=6)  # short names
    shared_key: str = Field(max_length=256)  # manufacturer+MPN, vendor legal name, product name, lane...
    evidence: list[str] = Field(default_factory=list, max_length=12)  # table refs, "<short>:<ref>"
    detail: str = Field(default="", max_length=2000)
    estimated_annual_value: float | None = None
    confidence: float = Field(ge=0, le=1)


class AnalyzeOutput(BaseModel):
    opportunities: list[Opportunity] = Field(default_factory=list, max_length=30)
    rejected: list[str] = Field(default_factory=list, max_length=30)  # look-alikes the analyst ruled out


SYSTEM = (
    "You are Vista's Portfolio Analyst comparing the same table type across sister companies owned by one "
    "private-equity firm. Find opportunities only where the shared key is exactly the same thing: the same "
    "manufacturer AND manufacturer part number (a generic '6205' is not an SKF '6205-2RS1'), the same vendor legal "
    "entity (similar names are not the same vendor), the same software product (two products billed by one vendor "
    "are not overlap), the same customer entity. Equal prices are not a gap. List look-alikes you rejected. "
    'Respond with JSON only: {"opportunities": [{"kind": str, "title": str, "companies": [str], "shared_key": str, '
    '"evidence": [str], "detail": str, "estimated_annual_value": number|null, "confidence": 0..1}], "rejected": [str]}.'
)


def prepare(sector: str, tables_by_company: dict[Company, list[Table]]) -> Prompt:
    payload = {
        "sector": sector,
        "companies": [
            {
                "short": c.short,
                "data_quality_tier": c.tier,
                "tables": [
                    {"ref": t.ref, "columns": t.columns, "rows": t.rows[:MAX_ROWS_PER_TABLE], "total_rows": len(t.rows)} for t in tables
                ],
            }
            for c, tables in tables_by_company.items()
        ],
    }
    return Prompt(system=SYSTEM, user=json.dumps(payload, default=str), max_tokens=2500)


def parse(text: str) -> AnalyzeOutput:
    try:
        return AnalyzeOutput.model_validate_json(strip_fences(text))
    except ValidationError:
        return AnalyzeOutput()


def apply(output: AnalyzeOutput, known_shorts: set[str]) -> list[dict]:
    """opportunities rows: keeps only cross-company items with a shared key and known companies."""
    rows = []
    for o in output.opportunities:
        companies = sorted({c for c in o.companies if c in known_shorts})
        if len(companies) < 2 or not o.shared_key.strip():
            continue
        rows.append({**o.model_dump(), "companies": companies, "status": "open"})
    return rows
