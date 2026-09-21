"""Analyze phase — portfolio_analyst: one table type across companies in, opportunities out.

Proposal-only. The planted traps in the synthetic data are exactly the mistakes
a careless comparison makes (generic 6205 vs SKF 6205-2RS1; Apex Fastening
Systems vs Apex Fastener Corp; two products billed by one vendor), so apply()
requires an exact shared key and at least two companies per opportunity."""

import json
import re
from collections import defaultdict
from statistics import mean
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
    # indirect spend only: direct-material suppliers are sourcing decisions, not a vendor-master merge
    "vendor_consolidation": {"vendors", "corporate_vendors", "ap_vendor_invoices", "ap_vendor_invoices_indirect"},
    "software_overlap": {"software_subscriptions"},
    "freight_rate_gap": {"shipments"},
    "carrier_consolidation": {"carriers", "carrier_appointments", "policies"},
    "cross_sell": {"customers", "clients", "ship_to_locations"},
}


# ---- deterministic pre-screen ------------------------------------------------------------------
# Every opportunity kind is a join on one exact key. Code does the join across companies and hands
# the model a candidate list to confirm or reject; the model still owns the judgement (look-alikes,
# same-price items, unrelated products billed by one vendor) but no longer has to spot every
# shared key inside thousands of rows. That is where recall was lost.
MAX_CANDIDATES = 40
LEGAL_SUFFIX = re.compile(r"\b(inc|incorporated|llc|l\.l\.c|corp|corporation|co|company|ltd|limited|lp|plc|the)\b\.?", re.IGNORECASE)
GENERIC_WORDS = {
    "industrial",
    "supply",
    "supplies",
    "information",
    "management",
    "business",
    "advantage",
    "services",
    "service",
    "rentals",
    "rental",
    "group",
    "plant",
    "solutions",
    "international",
    "of",
    "and",
    "north",
    "america",
    "usa",
}
VENDOR_ALIASES = {"united parcel service": "ups", "federal express": "fedex"}


def _col(table: Table, *names: str) -> str | None:
    """First column whose snake_cased name is one of `names` ("Unit Cost", "UNIT_COST", "unit_cost" all match)."""
    wanted = set(names)
    for c in table.columns:
        if re.sub(r"[^a-z0-9]+", "_", c.lower()).strip("_") in wanted:
            return c
    return None


def _num(v) -> float | None:
    try:
        return float(str(v).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def vendor_key(name: str) -> str:
    """'W.W. Grainger, Inc.' / 'Grainger Industrial Supply' -> 'grainger'; 'United Parcel Service' -> 'ups'.
    The most specific word survives; generic trade words and legal suffixes go."""
    s = LEGAL_SUFFIX.sub(" ", name.lower())
    s = re.sub(r"[^a-z0-9 ]+", " ", s).strip()
    s = re.sub(r"\s+", " ", s)
    s = VENDOR_ALIASES.get(s, s)
    words = [w for w in s.split() if w not in GENERIC_WORDS and len(w) > 1]
    return words[0] if words else s


SITE_SUFFIX = re.compile(r"\s+-\s+.*$|\b(plant|warehouse|site|dc)\s*\d+.*$", re.IGNORECASE)


def customer_key(name: str) -> str:
    """'Cardinal Foods Group - Plant 12 - Hazleton, PA' / 'Cardinal Foods Corp.' / 'Cardinal Foods Plant 21' -> 'cardinal foods';
    'Fox River Paper Co.' and 'Fox River Climate Inc.' stay apart."""
    s = SITE_SUFFIX.sub(" ", name.lower())
    s = LEGAL_SUFFIX.sub(" ", s)
    return " ".join(w for w in re.sub(r"[^a-z0-9 ]+", " ", s).split() if w not in GENERIC_WORDS)


def _records() -> dict[str, set[str]]:
    return defaultdict(set)


def _add_record(records: dict[str, set[str]], short: str, row: dict) -> None:
    """Canonical rows carry their primary key as `id`; disk-read tables do not, and contribute no record ids."""
    rid = row.get("id")
    if rid:
        records[short].add(str(rid))


def _record_ids(records: dict[str, set[str]]) -> dict[str, list[str]]:
    return {k: sorted(v) for k, v in sorted(records.items())}


def _spread(prices: dict[str, float]) -> float:
    lo, hi = min(prices.values()), max(prices.values())
    return (hi - lo) / lo if lo else 0.0


def _screen_price_gap(tables_by_company: dict[Company, list[Table]]) -> list[dict]:
    by_mpn: dict[str, dict] = defaultdict(lambda: {"prices": {}, "manufacturers": set(), "evidence": set(), "records": _records()})
    for c, tables in tables_by_company.items():
        for t in tables:
            mpn_c = _col(t, "manufacturer_part_number", "mpn", "mfr_part_number")
            cost_c = _col(t, "unit_cost", "cost", "standard_cost")
            if not mpn_c or not cost_c:
                continue
            mfr_c = _col(t, "manufacturer", "mfr", "brand")
            for r in t.rows:
                mpn, cost = str(r.get(mpn_c) or "").strip().upper(), _num(r.get(cost_c))
                if not mpn or cost is None or cost <= 0:
                    continue
                e = by_mpn[mpn]
                e["prices"].setdefault(c.short, cost)
                e["evidence"].add(f"{c.short}:{t.ref}")
                _add_record(e["records"], c.short, r)
                if mfr_c and r.get(mfr_c):
                    e["manufacturers"].add(str(r[mfr_c]).strip())
    out = []
    for mpn, e in by_mpn.items():
        if len(e["prices"]) < 2 or len(e["manufacturers"]) > 1:
            continue
        spread = _spread(e["prices"])
        mfr = next(iter(e["manufacturers"]), "")
        out.append(
            {
                "shared_key": f"{mfr} {mpn}".strip(),
                "companies": sorted(e["prices"]),
                "unit_cost": e["prices"],
                "spread_pct": round(spread * 100),
                "note": "same unit cost everywhere — no gap" if spread == 0 else "",
                "evidence": sorted(e["evidence"]),
                "record_ids": _record_ids(e["records"]),
            }
        )
    return sorted(out, key=lambda x: -x["spread_pct"])


def _screen_vendors(tables_by_company: dict[Company, list[Table]]) -> list[dict]:
    by_key: dict[str, dict] = defaultdict(lambda: {"names": defaultdict(set), "evidence": set(), "records": _records()})
    for c, tables in tables_by_company.items():
        for t in tables:
            name_c = _col(t, "vendor_name", "supplier_name", "name", "vendor", "supplier", "payee")
            if not name_c:
                continue
            key_c = _col(t, "shared_key")
            for r in t.rows:
                name = str(r.get(name_c) or "").strip()
                if not name:
                    continue
                key = str(r.get(key_c) or "").strip().lower() if key_c else ""
                key = key or vendor_key(name)
                by_key[key]["names"][c.short].add(name)
                by_key[key]["evidence"].add(f"{c.short}:{t.ref}")
                _add_record(by_key[key]["records"], c.short, r)
    # A vendor master's explicit shared_key and the name-derived key of an AP file describe one vendor:
    # fold groups that share a (company, name) pair.
    merged: list[tuple[str, dict]] = []
    for key, e in sorted(by_key.items(), key=lambda kv: ("_" not in kv[0], kv[0])):
        pairs = {(c, n) for c, ns in e["names"].items() for n in ns}
        for _, target in merged:
            if pairs & {(c, n) for c, ns in target["names"].items() for n in ns}:
                for c, ns in e["names"].items():
                    target["names"][c] |= ns
                target["evidence"] |= e["evidence"]
                for s, ids in e["records"].items():
                    target["records"][s] |= ids
                break
        else:
            merged.append((key, e))
    out = []
    for key, e in merged:
        if len(e["names"]) < 2:
            continue
        out.append(
            {
                "shared_key": key,
                "companies": sorted(e["names"]),
                "names_by_company": {k: sorted(v) for k, v in sorted(e["names"].items())},
                "evidence": sorted(e["evidence"]),
                "record_ids": _record_ids(e["records"]),
            }
        )
    return sorted(out, key=lambda x: (-len(x["companies"]), x["shared_key"]))


def _screen_software(tables_by_company: dict[Company, list[Table]]) -> list[dict]:
    by_fn: dict[str, dict] = defaultdict(lambda: {"products": defaultdict(set), "evidence": set(), "records": _records()})
    for c, tables in tables_by_company.items():
        for t in tables:
            fn_c, prod_c = _col(t, "function", "category"), _col(t, "product", "product_name", "application")
            if not fn_c or not prod_c:
                continue
            for r in t.rows:
                fn, prod = str(r.get(fn_c) or "").strip(), str(r.get(prod_c) or "").strip()
                if not fn or not prod:
                    continue
                by_fn[fn.lower()]["products"][c.short].add(prod)
                by_fn[fn.lower()]["evidence"].add(f"{c.short}:{t.ref}")
                _add_record(by_fn[fn.lower()]["records"], c.short, r)
    out = []
    for fn, e in by_fn.items():
        n_products = len({p for ps in e["products"].values() for p in ps})
        dup_inside = any(len(ps) > 1 for ps in e["products"].values())
        if len(e["products"]) < 2 and not dup_inside:
            continue
        out.append(
            {
                "shared_key": fn,
                "companies": sorted(e["products"]),
                "products_by_company": {k: sorted(v) for k, v in sorted(e["products"].items())},
                "distinct_products": n_products,
                "note": "different products bought for one function — this is software_overlap by definition"
                if n_products > 1
                else "one product everywhere — portfolio contract candidate",
                "evidence": sorted(e["evidence"]),
                "record_ids": _record_ids(e["records"]),
            }
        )
    return sorted(out, key=lambda x: (-len(x["companies"]), x["shared_key"]))


def _screen_freight(tables_by_company: dict[Company, list[Table]]) -> list[dict]:
    per_lb: dict[str, float] = {}
    evidence = set()
    records = _records()
    for c, tables in tables_by_company.items():
        for t in tables:
            w_c, f_c = _col(t, "weight_lb", "weight"), _col(t, "freight_cost", "frt", "freight")
            if not w_c or not f_c:
                continue
            rates = []
            for r in t.rows:
                w, f = _num(r.get(w_c)), _num(r.get(f_c))
                if w and f and w > 0 and f > 0:
                    rates.append(f / w)
                    _add_record(records, c.short, r)
            if rates:
                per_lb[c.short] = round(mean(rates), 3)
                evidence.add(f"{c.short}:{t.ref}")
    if len(per_lb) < 2:
        return []
    return [
        {
            "shared_key": "parcel freight cost per lb",
            "companies": sorted(per_lb),
            "cost_per_lb": per_lb,
            "spread_pct": round(_spread(per_lb) * 100),
            "evidence": sorted(evidence),
            "record_ids": _record_ids(records),
        }
    ]


def _screen_customers(tables_by_company: dict[Company, list[Table]]) -> list[dict]:
    portfolio = {vendor_key(c.name) for c in tables_by_company} | {c.short.lower() for c in tables_by_company}
    by_key: dict[str, dict] = defaultdict(lambda: {"names": defaultdict(set), "evidence": set(), "records": _records()})
    for c, tables in tables_by_company.items():
        for t in tables:
            name_c = _col(t, "customer_name", "client_name", "name", "customer", "account_name")
            if not name_c:
                continue
            for r in t.rows:
                name = str(r.get(name_c) or "").strip()
                key = customer_key(name)
                if len(key.split()) < 2 or key.split()[0] in portfolio:
                    continue  # 'Keystone Chemical' at a sister company is a customer sharing a word, not Keystone
                by_key[key]["names"][c.short].add(name)
                by_key[key]["evidence"].add(f"{c.short}:{t.ref}")
                _add_record(by_key[key]["records"], c.short, r)
    out = []
    for key, e in by_key.items():
        if len(e["names"]) < 2:
            continue
        out.append(
            {
                "shared_key": key,
                "companies": sorted(e["names"]),
                "names_by_company": {k: sorted(v) for k, v in sorted(e["names"].items())},
                "evidence": sorted(e["evidence"]),
                "record_ids": _record_ids(e["records"]),
            }
        )
    return sorted(out, key=lambda x: (-len(x["companies"]), x["shared_key"]))


SCREENS = {
    "purchasing_price_gap": _screen_price_gap,
    "vendor_consolidation": _screen_vendors,
    "software_overlap": _screen_software,
    "freight_rate_gap": _screen_freight,
    "cross_sell": _screen_customers,
}


def screen(kind: str, tables_by_company: dict[Company, list[Table]]) -> list[dict]:
    """Exact-key join across companies for one kind. Candidates, not findings: the model confirms each."""
    fn = SCREENS.get(kind)
    return fn(tables_by_company)[:MAX_CANDIDATES] if fn else []


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
    opportunities: list[Opportunity] = Field(default_factory=list, max_length=60)
    rejected: list[str] = Field(default_factory=list, max_length=60)  # look-alikes the analyst ruled out


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
    "`candidates` is a code pre-screen: every exact shared key found in two or more companies, with the values "
    "behind it. Its keys already match exactly (same manufacturer part number, same vendor, same software "
    "function), so never reject a candidate as a partial match. Go through every candidate and either emit one "
    "opportunity for it (keep its shared_key and companies; use its numbers in detail and "
    "estimated_annual_value) or name it in `rejected` with the reason. Valid reasons: purchasing_price_gap with "
    "equal prices (no gap); a candidate note marking it as no gap; rows proving the entities are genuinely "
    "different. A candidate is by definition screened, so never label one 'not screened'. For software_overlap, different "
    "products doing the same job across sister companies IS the opportunity (CRM: HubSpot at one, Salesforce at "
    "another); the same product everywhere is also one (a portfolio contract). 'Distinct products' is never a "
    "reason to reject a software_overlap candidate. "
    "Do not skip candidates, do not merge several into one, and do not split one into several (one freight "
    "candidate is one opportunity listing every company). kind is always the look_for kind. When candidates are "
    "given, emit opportunities only for them: anything else you notice goes in `rejected` prefixed 'not screened:'. "
    "A candidate whose note says 'no gap' is a trap unless the rows prove otherwise. "
    "A candidate may carry `reviewer_findings`: structured data-quality and context findings from the per-company "
    "File Reviewer about the canonical records behind that candidate. Findings with effect 'degrade' mean the numbers "
    "are less trustworthy: keep the opportunity, lower confidence and say why in detail. Findings with effect 'enrich' "
    "add context that strengthens or explains the thesis: cite them in detail. Never invent findings. "
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


def prompt_candidate(cand: dict) -> dict:
    """Record ids are for deterministic intersection, not for the model; everything else in a candidate is prompt material."""
    return {k: v for k, v in cand.items() if k != "record_ids"}


def prepare(
    sector: str,
    tables_by_company: dict[Company, list[Table]],
    kind: str | None = None,
    candidates: list[dict] | None = None,
) -> Prompt:
    """`candidates` overrides the pre-screen (the Sector Merger passes finding-validated candidates)."""
    scoped = {c: tables_for_kind(kind, tables) if kind else tables for c, tables in tables_by_company.items()}
    if candidates is None:
        candidates = screen(kind, scoped) if kind else []
    payload = {
        "sector": sector,
        "look_for": kind or "any",
        "candidates": [prompt_candidate(c) for c in candidates],
        "companies": [
            {"short": c.short, "data_quality_tier": c.tier, "tables": [compact(t) for t in tables]} for c, tables in scoped.items()
        ],
    }
    return Prompt(system=SYSTEM, user=json.dumps(payload, default=str), max_tokens=5000)


def parse(text: str, kind: str | None = None) -> AnalyzeOutput:
    """Item-tolerant: one malformed opportunity (unknown kind, missing field) drops that item, not the batch.
    A per-kind call passes `kind`: every item is that kind, whatever label the model wrote on it."""
    try:
        raw = json.loads(strip_fences(text))
    except ValueError:
        return AnalyzeOutput()
    if not isinstance(raw, dict):
        return AnalyzeOutput()
    opportunities = []
    for item in raw.get("opportunities") or []:
        if isinstance(item, dict):
            item = {**item, "kind": kind or re.sub(r"[\s-]+", "_", str(item.get("kind", "")).strip().lower())}
        try:
            opportunities.append(Opportunity.model_validate(item))
        except ValidationError:
            continue
    rejected = [str(r) for r in (raw.get("rejected") or []) if isinstance(r, str | int | float)]
    return AnalyzeOutput(opportunities=opportunities[:60], rejected=rejected[:60])


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
