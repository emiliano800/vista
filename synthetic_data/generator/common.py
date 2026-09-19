"""Shared helpers for the Vista synthetic back-office data generator.

Everything is deterministic: each company gets its own seeded RNG so the
datasets are reproducible run-to-run.
"""
from __future__ import annotations

import csv
import json
import os
import random
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from typing import Any, Iterable

from openpyxl import Workbook

Row = dict[str, Any]

TODAY = date(2026, 3, 31)  # fixed "as of" date so all datasets agree

FIRST_NAMES = [
    "James", "Maria", "Robert", "Linda", "Michael", "Patricia", "David", "Jennifer",
    "William", "Elizabeth", "Richard", "Susan", "Joseph", "Jessica", "Thomas", "Sarah",
    "Daniel", "Karen", "Matthew", "Nancy", "Anthony", "Lisa", "Mark", "Betty", "Steven",
    "Sandra", "Paul", "Ashley", "Andrew", "Emily", "Kevin", "Donna", "Brian", "Michelle",
    "Priya", "Luis", "Aisha", "Wei", "Carlos", "Fatima", "Omar", "Grace", "Hiro", "Elena",
]
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Wilson", "Anderson", "Thomas", "Taylor",
    "Moore", "Jackson", "Martin", "Lee", "Thompson", "White", "Harris", "Clark", "Lewis",
    "Robinson", "Walker", "Young", "Allen", "King", "Wright", "Scott", "Nguyen", "Patel",
    "Okafor", "Kowalski", "Fitzgerald", "O'Brien", "Schneider", "Delgado", "Choi", "Nakamura",
]
STREETS = [
    "Main St", "Oak Ave", "Maple Dr", "Industrial Pkwy", "Commerce Blvd", "River Rd",
    "Washington St", "Lincoln Ave", "Park Pl", "Mill St", "Harbor Dr", "Front St",
    "Technology Way", "Enterprise Ct", "Airport Rd", "Union St", "Elm St", "Market St",
]
CITIES = [
    ("Hartford", "CT", "061"), ("Tampa", "FL", "336"), ("Scranton", "PA", "185"),
    ("Rockford", "IL", "611"), ("Allentown", "PA", "181"), ("Chattanooga", "TN", "374"),
    ("Toledo", "OH", "436"), ("Grand Rapids", "MI", "495"), ("Fort Wayne", "IN", "468"),
    ("Greenville", "SC", "296"), ("Knoxville", "TN", "379"), ("Richmond", "VA", "232"),
    ("Worcester", "MA", "016"), ("Providence", "RI", "029"), ("Albany", "NY", "122"),
    ("Orlando", "FL", "328"), ("Jacksonville", "FL", "322"), ("Harrisburg", "PA", "171"),
    ("Dayton", "OH", "454"), ("Peoria", "IL", "616"), ("Milwaukee", "WI", "532"),
]

DATE_FORMATS_MESSY = ["%m/%d/%Y", "%Y-%m-%d", "%d-%b-%Y", "%m/%d/%y", "%b %d, %Y"]


class Rng:
    def __init__(self, seed: int):
        self.r = random.Random(seed)

    def choice(self, seq):
        return self.r.choice(seq)

    def sample(self, seq, k):
        return self.r.sample(list(seq), k)

    def int(self, a, b):
        return self.r.randint(a, b)

    def money(self, a, b, step=0.01):
        return round(self.r.uniform(a, b) / step) * step

    def chance(self, p):
        return self.r.random() < p

    def person(self):
        return f"{self.choice(FIRST_NAMES)} {self.choice(LAST_NAMES)}"

    def address(self, city_pool=None):
        city, st, z3 = self.choice(city_pool or CITIES)
        return {
            "street": f"{self.int(10, 9899)} {self.choice(STREETS)}",
            "city": city,
            "state": st,
            "zip": f"{z3}{self.int(10, 99):02d}",
        }

    def phone(self):
        return f"({self.int(201, 989)}) {self.int(200, 999)}-{self.int(1000, 9999)}"

    def date_between(self, start: date, end: date) -> date:
        return start + timedelta(days=self.int(0, (end - start).days))

    def fein(self):
        return f"{self.int(10, 99)}-{self.int(1000000, 9999999)}"

    def vin(self):
        chars = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
        return "".join(self.choice(chars) for _ in range(17))


def email_for(name: str, domain: str) -> str:
    first, last = name.split(" ", 1)
    last = last.replace("'", "").replace(" ", "")
    return f"{first[0].lower()}{last.lower()}@{domain}"


def iso(d: date | datetime | None) -> str:
    if d is None:
        return ""
    return d.isoformat() if isinstance(d, date) and not isinstance(d, datetime) else d.strftime("%Y-%m-%dT%H:%M:%S")


def fmt_money(x: float) -> str:
    return f"{x:.2f}"


# --------------------------------------------------------------------------
# Answer key: every planted anomaly / synergy is registered here so we can
# check whether the agents find them later.
# --------------------------------------------------------------------------
@dataclass
class AnswerKey:
    items: list[dict] = field(default_factory=list)

    def add(self, key_id: str, kind: str, companies: list[str], title: str,
            description: str, evidence: list[str], expected_action: str,
            is_false_positive_trap: bool = False):
        self.items.append({
            "id": key_id,
            "kind": kind,  # data_quality | commission | certificate | purchasing | software | cross_sell | ap_match | inventory | trap
            "companies": companies,
            "title": title,
            "description": description,
            "evidence": evidence,
            "expected_action": expected_action,
            "is_false_positive_trap": is_false_positive_trap,
        })


# --------------------------------------------------------------------------
# Emitter: collects datasets for one company, then writes them to disk with
# tier-specific degradation applied.
# --------------------------------------------------------------------------
@dataclass
class Dataset:
    folder: str          # workflow folder, e.g. "04_renewals"
    name: str            # logical file name without extension, e.g. "renewals"
    rows: list[Row]
    fmt: str = "csv"     # csv | json | xlsx | eml | txt
    text: str | None = None  # for txt / eml
    notes: str | None = None


class Emitter:
    def __init__(self, root: str, company_slug: str, tier: str, rng: Rng):
        self.root = root
        self.slug = company_slug
        self.tier = tier  # high | medium | low
        self.rng = rng
        self.datasets: list[Dataset] = []
        self.dropped: list[str] = []
        self.merged_into_workbook: list[str] = []
        self.paths: dict[str, str] = {}  # "folder/dataset" -> relative path actually written

    def add(self, folder: str, name: str, rows: list[Row], fmt: str = "csv", notes: str | None = None):
        self.datasets.append(Dataset(folder, name, rows, fmt, notes=notes))

    def add_text(self, folder: str, name: str, text: str, ext: str = "txt"):
        self.datasets.append(Dataset(folder, name, [], ext, text=text))

    # ---- tier degradation -------------------------------------------------
    def _messy_date(self, value: str) -> str:
        try:
            d = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return value
        return d.strftime(self.rng.choice(DATE_FORMATS_MESSY))

    def _degrade_rows(self, rows: list[Row], level: str, rename: dict[str, str] | None = None) -> list[Row]:
        if not rows or level == "high":
            return rows
        out: list[Row] = []
        keys = list(rows[0].keys())
        date_keys = [k for k in keys if k.endswith("_date") or k in ("date", "effective", "expiration", "timestamp")]
        money_keys = [k for k in keys if any(t in k for t in ("amount", "premium", "price", "cost", "total", "balance", "commission", "revenue", "payroll"))]
        for row in rows:
            r = dict(row)
            if level == "low":
                for k in date_keys:
                    if r.get(k) and self.rng.chance(0.55):
                        r[k] = self._messy_date(str(r[k]))
                for k in money_keys:
                    if r.get(k) not in (None, "") and self.rng.chance(0.4):
                        try:
                            r[k] = f"${float(r[k]):,.2f}"
                        except (TypeError, ValueError):
                            pass
                for k in keys:
                    if isinstance(r.get(k), str) and r[k] and self.rng.chance(0.06):
                        r[k] = r[k] + " " if self.rng.chance(0.5) else r[k].upper()
                for k in keys:
                    if r.get(k) not in (None, "") and self.rng.chance(0.02):
                        r[k] = ""
            elif level == "medium":
                for k in date_keys:
                    if r.get(k) and self.rng.chance(0.15):
                        r[k] = self._messy_date(str(r[k]))
                for k in keys:
                    if isinstance(r.get(k), str) and r[k] and self.rng.chance(0.02):
                        r[k] = r[k] + " "
            out.append(r)
        if level == "low" and len(out) > 8:
            # duplicated rows (re-keyed exports)
            for _ in range(max(1, len(out) // 25)):
                out.insert(self.rng.int(0, len(out) - 1), dict(self.rng.choice(out)))
        if rename:
            out = [{rename.get(k, k): v for k, v in r.items()} for r in out]
        return out

    # ---- writing ----------------------------------------------------------
    def write_all(self, drop: Iterable[str] = (), merge_to_workbook: Iterable[str] = (),
                  renames: dict[str, dict[str, str]] | None = None,
                  header_style: str = "snake", workbook_name: str = "MASTER_WORKBOOK_v7_FINAL (2).xlsx"):
        """Write all datasets.

        drop: dataset names that this company simply does not have.
        merge_to_workbook: dataset names that are written as sheets of one
            legacy multi-sheet workbook instead of separate files.
        renames: per-dataset column renames (mislabeling).
        header_style: snake | title | legacy (UPPER, truncated)
        """
        drop = set(drop)
        merge_to_workbook = set(merge_to_workbook)
        renames = renames or {}
        base = os.path.join(self.root, self.slug)
        if os.path.isdir(base):
            shutil.rmtree(base)
        os.makedirs(base)
        workbook_sheets: dict[str, list[Row]] = {}
        manifest: list[dict] = []

        for ds in self.datasets:
            if ds.name in drop:
                self.dropped.append(ds.name)
                continue
            folder = os.path.join(base, ds.folder)
            if ds.text is not None or ds.fmt in ("txt", "eml", "md"):
                os.makedirs(folder, exist_ok=True)
                path = os.path.join(folder, f"{ds.name}.{ds.fmt}")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(ds.text or "")
                manifest.append({"folder": ds.folder, "file": os.path.basename(path), "rows": None})
                self.paths[f"{ds.folder}/{ds.name}"] = f"{ds.folder}/{os.path.basename(path)}"
                continue

            rows = self._degrade_rows(ds.rows, self.tier, renames.get(ds.name))
            rows = self._restyle_headers(rows, header_style)
            if ds.name in merge_to_workbook:
                workbook_sheets[ds.name[:31]] = rows
                self.merged_into_workbook.append(ds.name)
                self.paths[f"{ds.folder}/{ds.name}"] = f"00_legacy_exports/{workbook_name}#{ds.name[:31]}"
                continue
            os.makedirs(folder, exist_ok=True)
            if ds.fmt == "json":
                path = os.path.join(folder, f"{ds.name}.json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(rows, f, indent=2, default=str)
            elif ds.fmt == "xlsx":
                path = os.path.join(folder, f"{ds.name}.xlsx")
                write_xlsx(path, {ds.name[:31]: rows})
            else:
                path = os.path.join(folder, f"{ds.name}.csv")
                write_csv(path, rows)
            manifest.append({"folder": ds.folder, "file": os.path.basename(path), "rows": len(rows)})
            self.paths[f"{ds.folder}/{ds.name}"] = f"{ds.folder}/{os.path.basename(path)}"

        if workbook_sheets:
            path = os.path.join(base, "00_legacy_exports", workbook_name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            write_xlsx(path, workbook_sheets)
            manifest.append({"folder": "00_legacy_exports", "file": os.path.basename(path),
                             "rows": sum(len(v) for v in workbook_sheets.values()),
                             "sheets": list(workbook_sheets.keys())})
        return manifest

    def resolve_evidence(self, key: "AnswerKey", company_short: str) -> None:
        """Rewrite 'folder/dataset' evidence refs for this company to the file
        (or legacy-workbook sheet) that was actually written; mark dropped ones."""
        for it in key.items:
            if company_short not in it["companies"]:
                continue
            out = []
            for ev in it["evidence"]:
                base, _, frag = ev.partition("#")
                stem, dot, ext = base.rpartition(".")
                if not dot or ext not in ("csv", "json", "xlsx", "eml", "txt", "md"):
                    stem = base
                if stem in self.paths:
                    p = self.paths[stem]
                    out.append(p if "#" in p or not frag else f"{p}#{frag}")
                elif stem.rsplit("/", 1)[-1] in self.dropped:
                    out.append(f"{base} (NOT AVAILABLE - dataset missing at this company)")
                else:
                    out.append(ev)
            it["evidence"] = out

    def _restyle_headers(self, rows: list[Row], style: str) -> list[Row]:
        if not rows or style == "snake":
            return rows

        def conv(k: str) -> str:
            if style == "title":
                return k.replace("_", " ").title()
            if style == "legacy":
                return k.upper()[:18]
            return k
        return [{conv(k): v for k, v in r.items()} for r in rows]


def write_csv(path: str, rows: list[Row]):
    if not rows:
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write("")
        return
    fieldnames: list[str] = []
    for r in rows:
        for k in r.keys():
            if k not in fieldnames:
                fieldnames.append(k)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})


def write_xlsx(path: str, sheets: dict[str, list[Row]]):
    wb = Workbook()
    wb.remove(wb.active)
    wb.properties.created = datetime(2026, 3, 31, 8, 0, 0)  # deterministic output bytes
    wb.properties.modified = wb.properties.created
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        if not rows:
            continue
        fieldnames: list[str] = []
        for r in rows:
            for k in r.keys():
                if k not in fieldnames:
                    fieldnames.append(k)
        ws.append(fieldnames)
        for r in rows:
            ws.append([r.get(k, "") for k in fieldnames])
    wb.save(path)
    _normalize_zip_timestamps(path)


def _normalize_zip_timestamps(path: str) -> None:
    """Rewrite the xlsx zip with fixed member timestamps so regeneration is byte-identical."""
    tmp = path + ".tmp"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            fixed = zipfile.ZipInfo(info.filename, date_time=(2026, 3, 31, 8, 0, 0))
            fixed.compress_type = zipfile.ZIP_DEFLATED
            fixed.external_attr = info.external_attr
            data = src.read(info.filename)
            if info.filename == "docProps/core.xml":  # openpyxl stamps save time here
                data = re.sub(rb"<dcterms:modified[^>]*>[^<]*</dcterms:modified>",
                              b'<dcterms:modified xsi:type="dcterms:W3CDTF">2026-03-31T08:00:00Z</dcterms:modified>', data)
            dst.writestr(fixed, data)
    os.replace(tmp, path)


def eml(from_addr: str, to_addr: str, subject: str, body: str, sent: datetime, cc: str | None = None) -> str:
    m = EmailMessage()
    m["From"] = from_addr
    m["To"] = to_addr
    if cc:
        m["Cc"] = cc
    m["Subject"] = subject
    m["Date"] = sent.strftime("%a, %d %b %Y %H:%M:%S -0500")
    m.set_content(body)
    return m.as_string()


def gl_chart(kind: str) -> list[tuple[str, str, str]]:
    """(account_number, name, type) — a small, industry-appropriate chart of accounts."""
    common = [
        ("1000", "Operating Cash", "Asset"), ("1100", "Accounts Receivable", "Asset"),
        ("1500", "Prepaid Expenses", "Asset"), ("1700", "Fixed Assets", "Asset"),
        ("1750", "Accumulated Depreciation", "Asset"), ("2000", "Accounts Payable", "Liability"),
        ("2100", "Accrued Expenses", "Liability"), ("2300", "Payroll Liabilities", "Liability"),
        ("3000", "Retained Earnings", "Equity"), ("6000", "Salaries & Wages", "Expense"),
        ("6100", "Payroll Taxes", "Expense"), ("6200", "Employee Benefits", "Expense"),
        ("6300", "Rent", "Expense"), ("6400", "Software Subscriptions", "Expense"),
        ("6500", "Professional Fees", "Expense"), ("6600", "Insurance Expense", "Expense"),
        ("6700", "Office & Supplies", "Expense"), ("6800", "Travel & Entertainment", "Expense"),
        ("6900", "Depreciation Expense", "Expense"),
    ]
    if kind == "insurance":
        return common + [
            ("1200", "Premiums Receivable (Agency Bill)", "Asset"),
            ("1300", "Fiduciary / Premium Trust Cash", "Asset"),
            ("2200", "Premiums Payable to Carriers", "Liability"),
            ("2250", "Producer Commissions Payable", "Liability"),
            ("4000", "Commission Income - Agency Bill", "Revenue"),
            ("4010", "Commission Income - Direct Bill", "Revenue"),
            ("4100", "Contingent Commission Income", "Revenue"),
            ("4200", "Fee Income", "Revenue"),
            ("5000", "Producer Commission Expense", "Expense"),
            ("6650", "E&O Insurance", "Expense"),
        ]
    return common + [
        ("1200", "Inventory - Raw Materials", "Asset"), ("1210", "Inventory - WIP", "Asset"),
        ("1220", "Inventory - Finished Goods", "Asset"), ("1230", "Inventory - Purchased for Resale", "Asset"),
        ("2050", "GR/IR Clearing (Received Not Invoiced)", "Liability"),
        ("4000", "Product Sales - Manufactured", "Revenue"), ("4010", "Product Sales - Distributed", "Revenue"),
        ("4100", "Freight Revenue", "Revenue"), ("4900", "Sales Returns & Allowances", "Revenue"),
        ("5000", "COGS - Materials", "Expense"), ("5100", "COGS - Direct Labor", "Expense"),
        ("5200", "COGS - Overhead", "Expense"), ("5300", "Freight In", "Expense"),
        ("5400", "Inventory Adjustments / Scrap", "Expense"), ("5500", "Purchase Price Variance", "Expense"),
    ]


def month_ends(start: date, end: date) -> list[date]:
    out = []
    d = date(start.year, start.month, 1)
    while d <= end:
        nxt = date(d.year + (d.month // 12), (d.month % 12) + 1, 1)
        out.append(nxt - timedelta(days=1))
        d = nxt
    return out
