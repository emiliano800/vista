"""Import processors. API handlers only ever talk to `ImportProcessor`; the
deterministic demo processor below is the placeholder implementation and an
agent-backed processor can replace it (VISTA_IMPORT_PROCESSOR=agent) without
changing the import API or the canonical tables it writes to."""

from __future__ import annotations

import csv
import io
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import HTTPException

from vista.config import settings

# ---- Canonical dataset schema (mirrors src/web/public/lib/importer.js) ------------

DATASETS: dict[str, dict] = {
    "customers": {
        "label": "Customers",
        "entity": "customer",
        "fields": {
            "customer_name": {
                "label": "Customer name",
                "required": True,
                "aliases": ["client", "customer", "name", "account name", "company", "customer name", "bill to"],
            },
            "contact": {"label": "Contact", "aliases": ["contact", "attn", "primary contact"]},
            "email": {"label": "Email", "aliases": ["email", "e-mail", "contact email"]},
            "phone": {"label": "Phone", "aliases": ["phone", "tel", "telephone", "phone #", "mobile"]},
            "address": {"label": "Address", "aliases": ["address", "street", "addr", "service address"]},
            "city": {"label": "City", "aliases": ["city", "town", "city/state", "city, state", "location"]},
            "state": {"label": "State", "aliases": ["state", "st", "province"]},
            "zip": {"label": "ZIP", "aliases": ["zip", "zip code", "postal", "postal code"]},
            "service_type": {"label": "Service type", "aliases": ["service", "service type", "type", "segment", "line"]},
            "status": {"label": "Status", "aliases": ["status", "active", "active?"]},
            "source_customer_id": {"label": "Source customer ID", "aliases": ["customer id", "cust id", "id", "customer #"]},
        },
    },
    "invoices": {
        "label": "Invoices / Accounts Receivable",
        "entity": "invoice",
        "fields": {
            "source_invoice_number": {
                "label": "Source invoice number",
                "required": True,
                "aliases": ["inv #", "invoice", "invoice #", "invoice number", "inv no", "number", "ref"],
            },
            "customer_name": {
                "label": "Customer name",
                "required": True,
                "aliases": ["client", "customer", "customer name", "bill to", "account"],
            },
            "issue_date": {"label": "Issue date", "aliases": ["date", "invoice date", "issued", "inv date"]},
            "due_date": {"label": "Due date", "aliases": ["due", "due date", "pay by", "payable by"]},
            "amount": {"label": "Amount", "aliases": ["amount", "total", "invoice total", "amt", "billed"]},
            "outstanding_balance": {
                "label": "Outstanding balance",
                "aliases": ["balance", "amt due", "amount due", "open", "outstanding", "remaining"],
            },
            "status": {"label": "Status", "aliases": ["status", "paid?", "state"]},
        },
    },
    "vendors": {
        "label": "Vendors / Purchases",
        "entity": "vendor_purchase",
        "fields": {
            "vendor_name": {"label": "Vendor", "required": True, "aliases": ["vendor", "supplier", "payee", "vendor name"]},
            "sku": {"label": "SKU / item", "aliases": ["sku", "item", "item #", "part", "part #", "product code"]},
            "description": {"label": "Description", "aliases": ["description", "desc", "item description", "memo"]},
            "quantity": {"label": "Quantity", "aliases": ["qty", "quantity", "units"]},
            "unit": {"label": "Unit", "aliases": ["unit", "uom", "u/m"]},
            "unit_price": {"label": "Unit price", "aliases": ["price", "unit price", "rate", "cost", "unit cost"]},
            "date": {"label": "Date", "aliases": ["date", "po date", "purchase date", "ordered"]},
            "total": {"label": "Total", "aliases": ["total", "ext price", "extended", "line total", "amount"]},
        },
    },
    "subscriptions": {
        "label": "Software subscriptions",
        "entity": "subscription",
        "fields": {
            "product": {
                "label": "Vendor / product",
                "required": True,
                "aliases": ["product", "software", "vendor", "application", "tool", "service"],
            },
            "category": {"label": "Category", "aliases": ["category", "type", "function"]},
            "monthly_cost": {"label": "Monthly cost", "aliases": ["monthly $", "monthly", "cost", "monthly cost", "price", "mrr"]},
            "seats": {"label": "Seats", "aliases": ["seats", "users", "licenses", "licences"]},
            "renewal_date": {"label": "Renewal date", "aliases": ["renewal", "renews", "renewal date", "term end", "expires"]},
            "notes": {"label": "Contract notes", "aliases": ["notes", "terms", "contract", "restrictions"]},
        },
    },
    "profile": {"label": "Company profile", "entity": None, "fields": {}},
    "other": {"label": "Other", "entity": None, "fields": {}},
}
MAPPABLE = [k for k, d in DATASETS.items() if d["fields"]]


@dataclass
class Inspection:
    columns: list[str]
    rows: list[dict[str, str]]
    sheet: str
    dataset: str
    confidence: float


@dataclass
class ProposedMapping:
    source: str
    example: str
    target: str | None
    confidence: float

    @property
    def needs_review(self) -> bool:
        return not (self.target and self.confidence >= 0.9)


@dataclass
class NormalizedRecord:
    source_row: int
    raw: dict
    normalized: dict
    confidence: float


@dataclass
class DetectedException:
    exception_type: str
    description: str
    left: str
    right: str
    confidence: float
    actions: list[str]
    dataset: str
    left_row: int | None = None
    right_row: int | None = None
    record_rows: list[int] = field(default_factory=list)
    match_vendor: str | None = None
    candidates: list[dict] = field(default_factory=list)


class ImportProcessor(ABC):
    name = "abstract"

    @abstractmethod
    def inspect_file(self, filename: str, content: bytes) -> Inspection: ...

    @abstractmethod
    def propose_mappings(self, dataset: str, columns: list[str], rows: list[dict]) -> list[ProposedMapping]: ...

    @abstractmethod
    def normalize_records(self, dataset: str, rows: list[dict], mappings: list[ProposedMapping]) -> list[NormalizedRecord]: ...

    @abstractmethod
    def identify_exceptions(
        self, dataset: str, records: list[NormalizedRecord], existing_vendors: list[str]
    ) -> list[DetectedException]: ...


# ---- Deterministic placeholder --------------------------------------------------


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9#$/?]+", " ", str(s).lower()).strip()


def parse_csv(text: str) -> tuple[list[str], list[dict[str, str]]]:
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        return [], []
    header = [h.strip() for h in rows[0]]
    body = [{h: (r[i].strip() if i < len(r) else "") for i, h in enumerate(header)} for r in rows[1:]]
    return header, body


def parse_xlsx(content: bytes) -> tuple[str, list[str], list[dict[str, str]]]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    it = ws.iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(it, [])]
    body = []
    for r in it:
        if not any(v not in (None, "") for v in r):
            continue
        body.append({h: ("" if v is None else str(v).strip()) for h, v in zip(header, r, strict=False) if h})
    return ws.title, [h for h in header if h], body


STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA", "mass": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}  # fmt: skip


def normalize_text(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def normalize_state(v) -> str:
    s = str(v or "").strip().rstrip(".")
    if re.fullmatch(r"[A-Za-z]{2}", s):
        return s.upper()
    return STATES.get(s.lower(), s)


def normalize_city_state(v) -> tuple[str, str]:
    s = normalize_text(v)
    m = re.match(r"^(.*?)[,\s]+([A-Za-z.]{2,14})$", s)
    if not m:
        return s, ""
    state = normalize_state(m.group(2))
    if not re.fullmatch(r"[A-Z]{2}", state):
        return s, ""
    return m.group(1).strip().rstrip(","), state


def normalize_phone(v) -> str:
    digits = re.sub(r"\D", "", str(v or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return str(v or "").strip()
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def normalize_date(v) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", s)
    if m:
        year = f"20{m.group(3)}" if len(m.group(3)) == 2 else m.group(3)
        return f"{year}-{m.group(1).zfill(2)}-{m.group(2).zfill(2)}"
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s


def normalize_money(v) -> float:
    s = str(v or "").strip()
    if not s:
        return 0.0
    negative = (s.startswith("(") and s.endswith(")")) or s.startswith("-")
    cleaned = re.sub(r"[^0-9.]", "", s)
    try:
        n = float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0
    return -n if negative else n


def normalize_status(v) -> str:
    s = str(v or "").strip().lower()
    if s in ("y", "yes", "true", "1", "active", "open"):
        return "open" if s == "open" else "active"
    if s in ("n", "no", "false", "0", "inactive", "closed"):
        return "inactive"
    if s in ("paid", "pd", "settled"):
        return "paid"
    if s in ("overdue", "late", "past due"):
        return "overdue"
    return s


def normalize_name(v) -> str:
    s = normalize_text(v)
    s = re.sub(r"\b(l\.?l\.?c\.?|inc\.?|co\.?|corp\.?|ltd\.?)\b", lambda m: m.group(0).replace(".", "").upper(), s, flags=re.I)
    return re.sub(r"\s*,\s*", ", ", s)


DATE_FIELDS = {"issue_date", "due_date", "date", "renewal_date"}
MONEY_FIELDS = {"amount", "outstanding_balance", "unit_price", "total", "monthly_cost"}
COUNT_FIELDS = {"quantity", "seats"}
NAME_FIELDS = {"customer_name", "vendor_name"}


def _name_key(s: str) -> str:
    k = normalize_text(s).lower()
    k = re.sub(r"[.,']", "", k)
    k = re.sub(r"\b(llc|inc|co|corp|ltd|the|services?|enterprises?|supply|hvac|boston|w w)\b", "", k)
    k = re.sub(r"#\d+", "", k)
    return re.sub(r"\s+", " ", k).strip()


def similarity(a: str, b: str) -> float:
    x, y = _name_key(a), _name_key(b)
    if not x or not y:
        return 0.0
    if x == y:
        return 0.93
    wa, wb = set(x.split(" ")), set(y.split(" "))
    j = len(wa & wb) / len(wa | wb)
    return 0.6 + j * 0.3 if j >= 0.5 else 0.0


class DeterministicDemoImportProcessor(ImportProcessor):
    """Header-alias mapping, rule-based normalisation and similarity-based
    exception detection. No model calls; every output is reproducible."""

    name = "demo"

    def inspect_file(self, filename: str, content: bytes) -> Inspection:
        lower = filename.lower()
        if lower.endswith((".xlsx", ".xlsm")):
            sheet, columns, rows = parse_xlsx(content)
        elif lower.endswith((".csv", ".txt")):
            sheet = "Sheet1"
            columns, rows = parse_csv(content.decode("utf-8-sig", errors="replace"))
        else:
            raise HTTPException(415, f"{filename}: only CSV and XLSX exports are parsed by the {self.name} import processor")
        if not columns:
            raise HTTPException(422, f"{filename}: no header row found")
        dataset, confidence = self.detect_dataset(columns, filename)
        return Inspection(columns, rows, sheet, dataset if confidence >= 0.5 else "other", confidence)

    @staticmethod
    def detect_dataset(columns: list[str], filename: str = "") -> tuple[str, float]:
        heads = [_norm(c) for c in columns]
        name = filename.lower()
        bonus_re = {
            "invoices": r"ar|invoice|receivable",
            "customers": r"customer|client|account",
            "vendors": r"vendor|purchase|po|supplier",
            "subscriptions": r"software|subscription|saas|license",
        }
        scores = []
        for key in MAPPABLE:
            fields = DATASETS[key]["fields"].values()
            hits = sum(1 for h in heads if any(h in f["aliases"] for f in fields))
            bonus = 0.15 if re.search(bonus_re[key], name) else 0.0
            shared = sum(1 for h in heads if h in ("date", "total", "amount", "status", "type", "id"))
            score = ((hits - shared * 0.4) / len(heads) + bonus) if heads else 0.0
            scores.append((key, score))
        scores.sort(key=lambda kv: -kv[1])
        best, runner = scores[0], scores[1] if len(scores) > 1 else (None, 0.0)
        margin = best[1] - runner[1]
        confidence = max(0.0, min(0.99, best[1] * 0.75 + margin * 0.6 + 0.1))
        return best[0], round(confidence, 2)

    def propose_mappings(self, dataset: str, columns: list[str], rows: list[dict]) -> list[ProposedMapping]:
        fields = DATASETS.get(dataset, DATASETS["other"])["fields"]
        used: set[str] = set()
        out = []
        for column in columns:
            head = _norm(column)
            target, confidence = None, 0.0
            for key, d in fields.items():
                if key in used:
                    continue
                aliases = d["aliases"]
                idx = aliases.index(head) if head in aliases else -1
                if idx == 0 or head == _norm(d["label"]):
                    target, confidence = key, 0.99
                    break
                if idx > 0 and confidence < 0.97:
                    target, confidence = key, 0.97
                if idx < 0 and any(a in head or head in a for a in aliases) and confidence < 0.72:
                    target, confidence = key, 0.72
            if not target and re.match(r"^acct|^acc|account", head):
                confidence = 0.51
            if target:
                used.add(target)
            example = next((r[column] for r in rows if r.get(column)), "")
            out.append(ProposedMapping(column, example, target, confidence))
        return out

    def normalize_records(self, dataset: str, rows: list[dict], mappings: list[ProposedMapping]) -> list[NormalizedRecord]:
        fields = DATASETS.get(dataset, DATASETS["other"])["fields"]
        active = [m for m in mappings if m.target and m.target in fields]
        confidence = min([m.confidence for m in active] + [1.0])
        out = []
        for i, row in enumerate(rows):
            record: dict = {}
            raw: dict = {}
            for m in active:
                value = row.get(m.source, "")
                raw[m.source] = value
                t = m.target
                if t == "phone":
                    v = normalize_phone(value)
                elif t == "state":
                    v = normalize_state(value)
                elif t == "city":
                    v, st = normalize_city_state(value)
                    if st and not record.get("state"):
                        record["state"] = st
                elif t in DATE_FIELDS:
                    v = normalize_date(value)
                elif t in MONEY_FIELDS:
                    v = normalize_money(value)
                elif t in COUNT_FIELDS:
                    v = round(normalize_money(value))
                elif t == "status":
                    v = normalize_status(value)
                elif t in NAME_FIELDS:
                    v = normalize_name(value)
                else:
                    v = normalize_text(value)
                record[t] = v
            out.append(NormalizedRecord(i + 2, raw, record, confidence))
        return out

    def identify_exceptions(self, dataset: str, records: list[NormalizedRecord], existing_vendors: list[str]) -> list[DetectedException]:
        out: list[DetectedException] = []
        if dataset == "customers":
            names = [(r.source_row, r.normalized.get("customer_name", "")) for r in records]
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    s = similarity(names[i][1], names[j][1])
                    if s >= 0.85 and names[i][1] != names[j][1]:
                        out.append(
                            DetectedException(
                                "Possible duplicate customer",
                                f"'{names[i][1]}' (row {names[i][0]}) and '{names[j][1]}' (row {names[j][0]}) look like the same customer.",
                                names[i][1],
                                names[j][1],
                                round(s, 2),
                                ["Merge", "Keep separate", "Skip"],
                                "customers",
                                left_row=names[i][0],
                                right_row=names[j][0],
                                record_rows=[names[i][0], names[j][0]],
                            )
                        )
                        if len(out) >= 8:
                            return out
        if dataset == "vendors":
            seen: list[str] = []
            for r in records:
                v = r.normalized.get("vendor_name", "")
                if not v or v in seen:
                    continue
                seen.append(v)
                ranked = sorted(((e, similarity(v, e)) for e in existing_vendors), key=lambda es: -es[1])
                if not ranked:
                    continue
                best, s = ranked[0]
                candidates = [{"name": e, "similarity": round(sc, 2)} for e, sc in ranked[:3] if sc > 0]
                if s >= 0.6 and _name_key(v) != _name_key(best) and s < 0.93:
                    out.append(
                        DetectedException(
                            "Possible vendor match",
                            f"'{v}' may be portfolio vendor '{best}'.",
                            v,
                            best,
                            round(s, 2),
                            ["Match", "Keep separate", "Skip"],
                            "vendors",
                            match_vendor=best,
                            candidates=candidates,
                        )
                    )
                elif _name_key(v) == _name_key(best) and v != best:
                    out.append(
                        DetectedException(
                            "Vendor name variant",
                            f"'{v}' is a spelling variant of portfolio vendor '{best}'.",
                            v,
                            best,
                            0.9,
                            ["Match", "Keep separate", "Skip"],
                            "vendors",
                            match_vendor=best,
                            candidates=candidates,
                        )
                    )
        return out


class AgentImportProcessor(ImportProcessor):
    """Reserved for the LLM-backed processor. Same contract, not yet implemented."""

    name = "agent"

    def inspect_file(self, filename: str, content: bytes) -> Inspection:
        raise HTTPException(501, "Agent import processor is not available yet")

    def propose_mappings(self, dataset, columns, rows):
        raise HTTPException(501, "Agent import processor is not available yet")

    def normalize_records(self, dataset, rows, mappings):
        raise HTTPException(501, "Agent import processor is not available yet")

    def identify_exceptions(self, dataset, records, existing_vendors):
        raise HTTPException(501, "Agent import processor is not available yet")


def get_processor(name: str | None = None) -> ImportProcessor:
    name = name or settings.import_processor
    if name == "agent" and settings.enable_agent_import:
        return AgentImportProcessor()
    return DeterministicDemoImportProcessor()
