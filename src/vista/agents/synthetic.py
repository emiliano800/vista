"""Read-only access to synthetic_data/: the only data source for now.

Sector = insurance_broking | industrial_goods. Division = a numbered workflow
folder (01_clients_crm, 11_billing_ar, ...). Tables are csv files or xlsx
sheets; low-tier companies keep core tables only as sheets of a legacy workbook.
"""

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[3] / "synthetic_data"
SECTORS = ("insurance_broking", "industrial_goods")


@dataclass(frozen=True)
class Company:
    slug: str
    name: str
    short: str
    sector: str
    tier: str  # high|medium|low

    @property
    def folder(self) -> Path:
        return ROOT / self.sector / self.slug

    def profile(self) -> dict:
        return json.loads((self.folder / "00_company" / "company_profile.json").read_text())

    def procedures(self) -> str:
        return (self.folder / "00_company" / "operating_procedures.md").read_text()


@dataclass(frozen=True)
class Dataset:
    company: Company
    division: str  # folder name
    file: str
    rows: int | None

    @property
    def path(self) -> Path:
        return self.company.folder / self.division / self.file

    @property
    def format(self) -> str:
        return self.path.suffix.lstrip(".").lower()

    @property
    def ref(self) -> str:
        """Evidence reference in the answer key's style: '<division>/<file>'."""
        return f"{self.division}/{self.file}"


@dataclass
class Table:
    ref: str  # '<division>/<file>' or '<division>/<file>#<sheet>'
    columns: list[str]
    rows: list[dict] = field(default_factory=list)

    @property
    def name(self) -> str:
        """Table type independent of container: 'items' for both 01_master_data/items.csv and legacy.xlsx#items."""
        if "#" in self.ref:
            return self.ref.rsplit("#", 1)[1].lower()
        return Path(self.ref).stem.lower()

    def column(self, name: str) -> list:
        return [r.get(name) for r in self.rows]


class AnswerItem(BaseModel):
    id: str
    kind: str
    companies: list[str]  # short names
    title: str
    description: str
    evidence: list[str]
    expected_action: str
    is_false_positive_trap: bool = False


def manifest(root: Path = ROOT) -> dict:
    return json.loads((root / "manifest.json").read_text())


def companies(root: Path = ROOT) -> list[Company]:
    out = []
    for sector, entries in manifest(root)["sectors"].items():
        for c in entries:
            out.append(Company(slug=c["slug"], name=c["name"], short=c["short"], sector=sector, tier=c["tier"]))
    return out


def company(short_or_slug: str, root: Path = ROOT) -> Company:
    key = short_or_slug.lower()
    for c in companies(root):
        if key in (c.slug, c.short.lower()):
            return c
    raise KeyError(f"unknown synthetic company {short_or_slug!r}")


def datasets(c: Company, root: Path = ROOT) -> list[Dataset]:
    entries = next(e for e in manifest(root)["sectors"][c.sector] if e["slug"] == c.slug)["files"]
    return [Dataset(company=c, division=f["folder"], file=f["file"], rows=f["rows"]) for f in entries]


def divisions(c: Company, root: Path = ROOT) -> list[str]:
    return sorted({d.division for d in datasets(c, root)})


def read_csv(path: Path, ref: str) -> Table:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    return Table(ref=ref, columns=list(reader.fieldnames or []), rows=rows)


def read_xlsx(path: Path, ref: str) -> list[Table]:
    """One Table per sheet; ref gets '#<sheet>' appended, matching the answer key."""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    tables = []
    for ws in wb.worksheets:
        it = ws.iter_rows(values_only=True)
        header = next(it, None)
        if header is None:
            continue
        cols = ["" if h is None else str(h) for h in header]
        rows = [dict(zip(cols, r, strict=False)) for r in it if any(v is not None for v in r)]
        tables.append(Table(ref=f"{ref}#{ws.title}", columns=cols, rows=rows))
    return tables


def read_tables(ds: Dataset) -> list[Table]:
    """Tabular datasets -> list of Table (csv: one; xlsx: one per sheet). Others -> []."""
    if ds.format == "csv":
        return [read_csv(ds.path, ds.ref)]
    if ds.format == "xlsx":
        return read_xlsx(ds.path, ds.ref)
    return []


def read_text(ds: Dataset) -> str:
    return ds.path.read_text(encoding="utf-8", errors="replace")


def tables_for(c: Company, division: str | None = None, root: Path = ROOT) -> list[Table]:
    out: list[Table] = []
    for ds in datasets(c, root):
        if division and ds.division != division:
            continue
        out.extend(read_tables(ds))
    return out


def answer_key(root: Path = ROOT) -> list[AnswerItem]:
    """Evaluation set only. Never pass this to a phase's prepare()."""
    return [AnswerItem.model_validate(i) for i in json.loads((root / "answer_key.json").read_text())]
