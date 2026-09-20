"""Analyze phase — portfolio_analyst: one table type across companies in, opportunities out.

Proposal-only. The planted traps in the synthetic data are exactly the mistakes
a careless comparison makes (generic 6205 vs SKF 6205-2RS1; Apex Fastening
Systems vs Apex Fastener Corp; two products billed by one vendor), so apply()
requires an exact shared key and at least two companies per opportunity."""

import json
import re
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
MAX_ROWS_PER_TABLE = 250
KEY_COLUMN = re.compile(
    r"id$|name|manufacturer|part|mpn|sku|descr|price|cost|amount|qty|quantity|uom|vendor|supplier|carrier|product|"
    r"function|category|segment|weight|service|city|state|lane|zone|renewal|term",
    re.IGNORECASE,
)

# One model call per kind; a sector only gets the kinds its data can support.
SECTOR_KINDS: dict[str, list[str]] = {
    "industrial_goods": ["purchasing_price_gap", "vendor_consolidation", "software_overlap", "freight_rate_gap", "cross_sell"],
    "insurance_broking": ["vendor_consolidation", "software_overlap", "carrier_consolidation", "cross_sell"],
}

# Which table types feed each opportunity kind; anything else is noise for that comparison.
KIND_TABLES: dict[str, set[str]] = {
    "purchasing_price_gap": {"items", "quickbooks_item_list_export"},  # item masters carry manufacturer, MPN and unit cost
    "vendor_consolidation": {"suppliers", "vendors", "corporate_vendors", "ap_vendor_invoices", "ap_vendor_invoices_indirect"},
    "software_overlap": {"software_subscriptions"},
    "freight_rate_gap": {"shipments"},
    "carrier_consolidation": {"carriers", "carrier_appointments", "policies"},
    "cross_sell": {"customers", "clients", "ship_to_locations"},
}


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
    "manufacturer AND manufacturer part number (a generic '6205' is not an SKF '6205-2RS1'); the same vendor legal "
    "entity (similar names are not the same vendor); the same software function (software_overlap groups by the "
    "function/category column: several products doing the same job across sister companies, or two subscriptions "
    "for one product inside a company; two unrelated products billed by one vendor are not overlap); the same "
    "customer group (plants or sites of one parent company count; unrelated companies sharing a word do not). "
    "Sister companies keep separate vendor masters, so the same vendor legitimately appears with different ids, "
    "tax ids, payment terms or spellings across companies; that is a consolidation opportunity, not a reason to "
    "reject. Equal prices are not a gap. Use only the listed kinds. evidence entries are the table refs exactly as "
    "given (e.g. 'Keystone:01_master_data/items.csv'). List look-alikes you rejected. "
    'Respond with JSON only: {"opportunities": [{"kind": str, "title": str, "companies": [str], "shared_key": str, '
    '"evidence": [str], "detail": str, "estimated_annual_value": number|null, "confidence": 0..1}], "rejected": [str]}.'
)


def compact(table: Table) -> dict:
    """Key columns only, duplicate projections dropped, then capped: keeps a 1 800-row PO-line table comparable."""
    cols = [c for c in table.columns if KEY_COLUMN.search(c)] or table.columns
    seen: set[tuple] = set()
    rows = []
    for r in table.rows:
        proj = tuple(r.get(c) for c in cols)
        if proj in seen:
            continue
        seen.add(proj)
        rows.append(dict(zip(cols, proj, strict=True)))
        if len(rows) >= MAX_ROWS_PER_TABLE:
            break
    return {"ref": table.ref, "columns": cols, "rows": rows, "total_rows": len(table.rows)}


def tables_for_kind(kind: str, tables: list[Table]) -> list[Table]:
    return [t for t in tables if t.name in KIND_TABLES[kind]]


def prepare(sector: str, tables_by_company: dict[Company, list[Table]], kind: str | None = None) -> Prompt:
    payload = {
        "sector": sector,
        "look_for": kind or "any",
        "companies": [
            {
                "short": c.short,
                "data_quality_tier": c.tier,
                "tables": [compact(t) for t in (tables_for_kind(kind, tables) if kind else tables)],
            }
            for c, tables in tables_by_company.items()
        ],
    }
    return Prompt(system=SYSTEM, user=json.dumps(payload, default=str), max_tokens=2500)


def parse(text: str) -> AnalyzeOutput:
    """Item-tolerant: one malformed opportunity (unknown kind, missing field) drops that item, not the batch."""
    try:
        raw = json.loads(strip_fences(text))
    except ValueError:
        return AnalyzeOutput()
    if not isinstance(raw, dict):
        return AnalyzeOutput()
    opportunities = []
    for item in raw.get("opportunities") or []:
        try:
            opportunities.append(Opportunity.model_validate(item))
        except ValidationError:
            continue
    rejected = [str(r) for r in (raw.get("rejected") or []) if isinstance(r, str | int | float)]
    return AnalyzeOutput(opportunities=opportunities[:30], rejected=rejected[:30])


def match_refs(evidence: list[str], known_refs: set[str]) -> list[str]:
    """Model evidence strings -> known table refs. Accepts '<short>:<ref>', bare refs, and refs with trailing notes."""
    matched = []
    for e in evidence:
        for ref in known_refs:
            if ref in e and ref not in matched:
                matched.append(ref)
    return matched


def apply(output: AnalyzeOutput, known_shorts: set[str], known_refs: set[str] | None = None) -> list[dict]:
    """opportunities rows: keeps only cross-company items with a shared key and known companies.
    With known_refs, evidence is normalised to table refs; free-text evidence is kept in `notes`."""
    rows = []
    for o in output.opportunities:
        companies = sorted({c for c in o.companies if c in known_shorts})
        if len(companies) < 2 or not o.shared_key.strip():
            continue
        row = {**o.model_dump(), "companies": companies, "status": "open"}
        if known_refs is not None:
            refs = match_refs(o.evidence, known_refs)
            row["notes"] = [e for e in o.evidence if not any(r in e for r in refs)]
            row["evidence"] = refs or sorted(known_refs)
        rows.append(row)
    return rows
