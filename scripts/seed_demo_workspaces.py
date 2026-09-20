"""Seed the six synthetic portfolio companies as separate demo workspaces.

Each company in synthetic_data/ becomes its own workspace (tenant + company +
owner access key) whose recording reports are generated from that company's own
back-office data: client/supplier names, PO numbers and the AMS/ERP the company
actually uses. Reports go through the real task-mining pipeline and the real
upload API, so everything the workspace shows is backed by stored bundles.

Usage:
  1. Provision workspaces (locally, against the DB in .env):
       uv run python scripts/seed_demo_workspaces.py --provision --credentials /tmp/creds.json
     (In AWS, run deploy/aws/manage.sh create-workspace per company instead and
      assemble the same JSON list by hand.)
  2. Upload reports through the API:
       uv run python scripts/seed_demo_workspaces.py --upload --credentials /tmp/creds.json --base-url http://localhost:8000
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import random
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from taskmining.abstraction import ActivityRule  # noqa: E402
from taskmining.models import EventType, RawEvent  # noqa: E402
from taskmining.pipeline import Pipeline  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "synthetic_data"

COMPANIES = [
    {
        "slug": "meridian_risk_partners",
        "name": "Meridian Risk Partners, LLC",
        "sector": "insurance",
        "system": "Applied Epic",
        "email": "demo@meridianrisk.com",
        "entities": ["insurance_broking/meridian_risk_partners/01_clients_crm/clients.csv"],
        "refs": ["insurance_broking/meridian_risk_partners/02_policies_exposures/policies.csv"],
        "tracker": "Premium_Receivables_2026.xlsx",
        "cases": 18,
    },
    {
        "slug": "harborline_insurance_brokers",
        "name": "Harborline Insurance Brokers, Inc.",
        "sector": "insurance",
        "system": "AMS360",
        "email": "demo@harborlineins.com",
        "entities": ["insurance_broking/harborline_insurance_brokers/01_clients_crm/clients.csv"],
        "refs": ["insurance_broking/harborline_insurance_brokers/02_policies_exposures/*.csv"],
        "tracker": "AR_Followup_2026.xlsx",
        "cases": 14,
    },
    {
        "slug": "castlebrook_agency",
        "name": "Castlebrook Agency",
        "sector": "insurance",
        "system": "HawkSoft",
        "email": "demo@castlebrookagency.com",
        "entities": [
            "insurance_broking/castlebrook_agency/05_certificates/*.csv",
            "insurance_broking/castlebrook_agency/08_billing_ar/invoices.csv",
        ],
        "refs": ["insurance_broking/castlebrook_agency/08_billing_ar/invoices.csv"],
        "tracker": "CASTLEBROOK BILLING MASTER v3.xlsx",
        "cases": 9,
    },
    {
        "slug": "northfield_industrial_components",
        "name": "Northfield Industrial Components, Inc.",
        "sector": "industrial",
        "system": "Epicor Kinetic",
        "email": "demo@northfieldic.com",
        "entities": ["industrial_goods/northfield_industrial_components/03_procurement/suppliers.csv"],
        "refs": ["industrial_goods/northfield_industrial_components/03_procurement/purchase_orders.csv"],
        "tracker": "Three_Way_Match_2026.xlsx",
        "cases": 18,
    },
    {
        "slug": "keystone_bearing_and_drive",
        "name": "Keystone Bearing & Drive Co.",
        "sector": "industrial",
        "system": "NetSuite",
        "email": "demo@keystonebd.com",
        "entities": ["industrial_goods/keystone_bearing_and_drive/03_procurement/suppliers.csv"],
        "refs": ["industrial_goods/keystone_bearing_and_drive/03_procurement/purchase_orders.csv"],
        "tracker": "AP_Match_Tracker_2026.xlsx",
        "cases": 14,
    },
    {
        "slug": "ridgeway_fasteners_and_supply",
        "name": "Ridgeway Fasteners & Supply",
        "sector": "industrial",
        "system": "QuickBooks",
        "email": "demo@ridgewayfast.com",
        "entities": [
            "industrial_goods/ridgeway_fasteners_and_supply/04_receiving_ap/*.csv",
            "industrial_goods/ridgeway_fasteners_and_supply/01_master_data/*.csv",
        ],
        "refs": ["industrial_goods/ridgeway_fasteners_and_supply/03_procurement/*.csv"],
        "tracker": "RIDGEWAY MASTER (Fishbowl sync) v7 FINAL (2).xlsx",
        "cases": 9,
    },
]

NAME_COLUMN = re.compile(r"(client|supplier|vendor|customer|holder|insured).*(name)|^(name)$", re.I)
ID_COLUMN = re.compile(r"(client|supplier|vendor|po|policy|invoice).*(id|number|num)", re.I)


def _read_column(path: Path, pattern: re.Pattern) -> list[str]:
    with path.open(newline="", encoding="utf-8", errors="replace") as fp:
        reader = csv.DictReader(fp)
        column = next((c for c in reader.fieldnames or [] if pattern.search(c.strip())), None)
        if column is None:
            return []
        seen: dict[str, None] = {}
        for row in reader:
            value = (row.get(column) or "").strip()
            if value:
                seen.setdefault(value)
        return list(seen)


def load_values(patterns: list[str], column: re.Pattern, limit: int = 60) -> list[str]:
    for pattern in patterns:
        for path in sorted(glob.glob(str(DATA / pattern))):
            values = _read_column(Path(path), column)
            if values:
                return values[:limit]
    return []


class ThemedSource:
    """Desktop sessions themed to one portfolio company: invoices arrive by
    email, get checked against the PDF, entered in the company's AMS/ERP and
    sometimes tracked in a spreadsheet — referencing the company's real
    (synthetic) clients/suppliers and document numbers."""

    def __init__(self, company: dict, entities: list[str], refs: list[str], n_cases: int, seed: int, start: datetime):
        self.company, self.entities, self.refs = company, entities, refs
        self.n_cases, self.rng, self.start = n_cases, random.Random(seed), start
        self.users = ["dana", "marco", "priya"]
        self._events: list[RawEvent] | None = None

    def events(self):
        if self._events is None:
            out: list[RawEvent] = []
            t = self.start
            for i in range(self.n_cases):
                t += timedelta(seconds=self.rng.randint(20, 240))
                t = self._case(out, t, i)
            self._events = out
        return list(self._events)

    def _e(self, out, t, user, typ, app, title, element="", text=""):
        out.append(RawEvent(t, user, typ, app, title, "", element, text, {}))

    def _case(self, out, t, i) -> datetime:
        r, ev = self.rng, self._e
        c = self.company
        user = r.choice(self.users)
        inv = f"INV-{r.randint(20260100, 20269999)}"
        entity = r.choice(self.entities)
        ref = r.choice(self.refs) if self.refs else ""
        system = c["system"]
        insurance = c["sector"] == "insurance"
        subject = f"Premium invoice {inv} - {entity}" if insurance else f"Invoice {inv} from {entity}"
        entry_title = f"Agency Bill Posting {inv} - {system}" if insurance else f"AP Invoice Entry {inv} ({ref}) - {system}"
        # 1. open the email and its attachment
        ev(out, t, user, EventType.FOCUS, "Outlook", f"{subject} - Message (HTML) - Outlook")
        t += timedelta(seconds=r.randint(2, 9))
        ev(out, t, user, EventType.CLICK, "Outlook", f"{subject} - Message (HTML) - Outlook", element="attachment")
        # 2. read the PDF, copy the invoice number
        t += timedelta(seconds=r.randint(2, 6))
        ev(out, t, user, EventType.FOCUS, "Acrobat", f"{inv}.pdf - Adobe Acrobat Reader")
        for _ in range(r.randint(1, 4)):
            t += timedelta(seconds=r.randint(2, 10))
            ev(out, t, user, EventType.SCROLL, "Acrobat", f"{inv}.pdf - Adobe Acrobat Reader")
        t += timedelta(seconds=r.randint(1, 4))
        ev(out, t, user, EventType.COPY, "Acrobat", f"{inv}.pdf - Adobe Acrobat Reader", text=inv)
        # 3. look the account up in the AMS/ERP
        if r.random() < 0.75:
            t += timedelta(seconds=r.randint(2, 6))
            lookup = f"Client Detail - {entity} - {system}" if insurance else f"Purchase Order {ref} - {system}"
            ev(out, t, user, EventType.FOCUS, system, lookup)
            for ch in str(entity)[:6]:
                t += timedelta(milliseconds=r.randint(80, 300))
                ev(out, t, user, EventType.KEY, system, lookup, text=ch)
        # 4. enter the invoice
        t += timedelta(seconds=r.randint(2, 6))
        ev(out, t, user, EventType.FOCUS, system, entry_title)
        t += timedelta(seconds=r.randint(1, 3))
        ev(out, t, user, EventType.PASTE, system, entry_title, element="Reference", text=inv)
        amount = f"{r.randint(90, 45000)}.{r.randint(0, 99):02d}"
        for ch in amount:
            t += timedelta(milliseconds=r.randint(80, 300))
            ev(out, t, user, EventType.KEY, system, entry_title, text=ch)
        if r.random() < 0.25:  # rework: recheck the PDF amount
            t += timedelta(seconds=r.randint(2, 5))
            ev(out, t, user, EventType.FOCUS, "Acrobat", f"{inv}.pdf - Adobe Acrobat Reader")
            t += timedelta(seconds=r.randint(3, 15))
            ev(out, t, user, EventType.FOCUS, system, entry_title)
        t += timedelta(seconds=r.randint(1, 4))
        ev(out, t, user, EventType.CLICK, system, entry_title, element="Post")
        # 5. sometimes track it in the spreadsheet
        if r.random() < 0.55:
            t += timedelta(seconds=r.randint(2, 6))
            ev(out, t, user, EventType.FOCUS, "Excel", f"{c['tracker']} - Excel")
            t += timedelta(seconds=r.randint(1, 3))
            ev(out, t, user, EventType.PASTE, "Excel", f"{c['tracker']} - Excel", text=inv)
            for ch in amount:
                t += timedelta(milliseconds=r.randint(80, 300))
                ev(out, t, user, EventType.KEY, "Excel", f"{c['tracker']} - Excel", text=ch)
        # 6. reply
        t += timedelta(seconds=r.randint(2, 6))
        ev(out, t, user, EventType.FOCUS, "Outlook", f"RE: {subject} - Message (HTML) - Outlook")
        for ch in f"Hi, {inv} has been posted."[:18]:
            t += timedelta(milliseconds=r.randint(60, 250))
            ev(out, t, user, EventType.KEY, "Outlook", f"RE: {subject} - Message (HTML) - Outlook", text=ch)
        t += timedelta(seconds=r.randint(1, 3))
        ev(out, t, user, EventType.CLICK, "Outlook", f"RE: {subject} - Message (HTML) - Outlook", element="Send")
        return t


def rules_for(company: dict) -> list[ActivityRule]:
    system = re.escape(company["system"])
    tracker = re.escape(company["tracker"][:18])
    if company["sector"] == "insurance":
        entry, lookup = "Agency Bill", "Client Detail"
        entry_name, lookup_name = f"Post Agency Bill ({company['system']})", f"Lookup Client ({company['system']})"
    else:
        entry, lookup = "AP Invoice Entry", "Purchase Order"
        entry_name, lookup_name = f"Enter AP Invoice ({company['system']})", f"Lookup PO ({company['system']})"
    return [
        ActivityRule("Send Reply Email", app="Outlook", element="^Send$", event_type=EventType.CLICK),
        ActivityRule("Write Reply Email", app="Outlook", title=r"^RE:"),
        ActivityRule("Read Invoice Email", app="Outlook", title=r"[Ii]nvoice"),
        ActivityRule("Read Email", app="Outlook"),
        ActivityRule("Review Invoice PDF", app="Acrobat"),
        ActivityRule(lookup_name, app=system, title=lookup),
        ActivityRule(f"{entry_name} - Post", app=system, title=entry, element="^Post$", event_type=EventType.CLICK),
        ActivityRule(entry_name, app=system, title=entry),
        ActivityRule("Update Tracker (Excel)", app="Excel", title=tracker),
        ActivityRule("Work in Excel", app="Excel"),
    ]


SESSION_STARTS = [datetime(2026, 3, 24, 9, 5, tzinfo=UTC), datetime(2026, 3, 26, 13, 40, tzinfo=UTC)]


def build_bundles(company: dict, tmp: Path) -> list[dict]:
    entities = load_values(company["entities"], NAME_COLUMN) or load_values(company["entities"], ID_COLUMN)
    if not entities:
        raise SystemExit(f"No entity values found for {company['slug']}")
    refs = load_values(company["refs"], ID_COLUMN, limit=40)
    bundles = []
    for n, start in enumerate(SESSION_STARTS, start=1):
        cases = company["cases"] if n == 1 else max(4, company["cases"] // 2)
        source = ThemedSource(company, entities, refs, cases, seed=hash(company["slug"]) % 10_000 + n, start=start)
        out = tmp / f"{company['slug']}-{n}"
        result = Pipeline(rules=rules_for(company)).run(source)
        result.write(out)
        raw = result.raw
        started, ended = min(e.timestamp for e in raw), max(e.timestamp for e in raw)
        apps: dict[str, float] = {}
        for step in result.steps:
            apps[step.app] = apps.get(step.app, 0) + step.duration_s
        bundles.append(
            {
                "version": 1,
                "manifest": {
                    "recording_id": f"{company['slug']}-session-{n}",
                    "started_at": started.isoformat(),
                    "ended_at": ended.isoformat(),
                    "active_seconds": int((ended - started).total_seconds()),
                    "processing": "done",
                    "counts": {"events": len(raw), "steps": len(result.steps)},
                    "apps": [{"app": app, "seconds": round(seconds, 1)} for app, seconds in sorted(apps.items())],
                },
                "summary": json.loads((out / "summary.json").read_text()),
                "event_log_csv": (out / "event_log.csv").read_text(),
            }
        )
    return bundles


def provision(credentials_path: Path) -> None:
    creds = []
    for company in COMPANIES:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "vista.manage",
                "create-workspace",
                "--firm",
                "Vista Capital Demo",
                "--company",
                company["name"],
                "--email",
                company["email"],
            ],
            capture_output=True,
            text=True,
            check=True,
            cwd=ROOT,
        )
        creds.append({"slug": company["slug"], **json.loads(result.stdout)})
        print(f"provisioned {company['slug']}")
    credentials_path.write_text(json.dumps(creds, indent=2))
    print(f"credentials written to {credentials_path}")


def upload(credentials_path: Path, base_url: str, tmp: Path) -> None:
    creds = {c["slug"]: c for c in json.loads(credentials_path.read_text())}
    with httpx.Client(base_url=base_url, timeout=120) as client:
        for company in COMPANIES:
            cred = creds[company["slug"]]
            headers = {"Authorization": f"Bearer {cred['access_key']}", "X-Vista-Request": "1"}
            for bundle in build_bundles(company, tmp):
                response = client.post(f"/api/deals/{cred['company_id']}/recordings", json=bundle, headers=headers)
                response.raise_for_status()
            listed = client.get(f"/api/deals/{cred['company_id']}/recordings", headers=headers)
            listed.raise_for_status()
            print(f"{company['slug']}: {len(listed.json())} reports live")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provision", action="store_true", help="create the six workspaces via vista.manage (local DB)")
    parser.add_argument("--upload", action="store_true", help="build themed report bundles and upload them via the API")
    parser.add_argument("--credentials", type=Path, required=True, help="JSON file of per-company credentials")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--tmp", type=Path, default=Path("/tmp/vista-demo-bundles"))
    args = parser.parse_args()
    if not (args.provision or args.upload):
        parser.error("choose --provision and/or --upload")
    if args.provision:
        provision(args.credentials)
    if args.upload:
        upload(args.credentials, args.base_url, args.tmp)


if __name__ == "__main__":
    main()
