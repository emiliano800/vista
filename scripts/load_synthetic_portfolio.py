"""Load the six canonical synthetic companies (synthetic_data/) into the PE analyst
workspace through the fact layer, then (optionally) run the interpretation layer.

    uv run python scripts/load_synthetic_portfolio.py --analyst-key <64 hex>
    uv run python scripts/load_synthetic_portfolio.py --only ridgeway --no-analyze

Fact layer: every CSV / workbook sheet whose name is in `DATASET_BY_TABLE` goes
through the *same* import pipeline the upload wizard uses (store file -> detect ->
propose mappings -> approve -> normalise -> exceptions -> commit), so each
canonical row carries `record_provenance` back to file, sheet, row and raw values.
Files that have no canonical home (claims, work orders, GL, ...) are skipped —
nothing is invented. Re-running is idempotent: a file+sheet whose content hash
already has a completed import job in that tenant is not imported twice.

Interpretation layer: `vista.portfolio.interpret.run_portfolio_interpretation`
enqueues one durable job per company (canonical File Reviewer); once every review in a
sector is terminal the barrier queues that sector's merge, reading only what the fact
layer wrote plus the successful reviewers' structured findings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vista.auth import Principal  # noqa: E402
from vista.config import settings  # noqa: E402
from vista.db import platform_session, tenant_session  # noqa: E402
from vista.jobs.worker import process_one  # noqa: E402
from vista.models.platform import Firm, FirmCompany, FirmMembership, Job, Tenant, User  # noqa: E402
from vista.models.tenant import (  # noqa: E402
    Customer,
    FieldMapping,
    ImportJob,
    InventoryBalance,
    Invoice,
    Policy,
    PurchaseOrder,
    PurchaseOrderLine,
    SourceFile,
    Subscription,
    Vendor,
    VendorPurchase,
)
from vista.portfolio import imports  # noqa: E402
from vista.portfolio.access import CompanyRef, FirmContext, load_firm_context  # noqa: E402
from vista.portfolio.interpret import release_sector_barrier, run_portfolio_interpretation  # noqa: E402
from vista.portfolio.processors import AUTO_CONFIDENCE, get_processor, workbook_sheets  # noqa: E402
from vista.portfolio.service import ensure_company_deal  # noqa: E402
from vista.security import token_digest  # noqa: E402
from vista.storage import ensure_bucket  # noqa: E402
from vista.tenancy import migrate_platform, migrate_tenant_schema  # noqa: E402

SYNTHETIC = ROOT / "synthetic_data"
MANIFEST = SYNTHETIC / "manifest.json"
NAMESPACE = uuid.UUID("7f0c2a52-6b3d-4b7e-9d0a-3c6f5e1b2a90")
FIRM = {"name": "Vista Capital Partners", "slug": "vista-capital"}
ANALYST = {"email": "analyst@vistacapital.example", "name": "Portfolio Analyst", "role": "analyst"}
# Deal close dates are a fact about the (fictional) holding firm, not about the
# companies, so they live here rather than in synthetic_data/.
ACQUIRED = {
    "meridian": "2023-06-30",
    "harborline": "2024-02-15",
    "castlebrook": "2024-11-01",
    "northfield": "2022-09-30",
    "keystone": "2023-12-15",
    "ridgeway": "2025-05-01",
}

# Source table name (CSV stem or workbook sheet) -> canonical dataset. Only these
# tables have a canonical home; everything else stays a source file for the data room.
DATASET_BY_TABLE: dict[str, str] = {
    # insurance broking
    "clients": "customers",
    "policies": "policies",
    "invoices": "invoices",
    "vendors": "vendor_master",
    "ap_vendor_invoices": "vendor_invoices",
    # industrial goods
    "customers": "customers",
    "customer_invoices": "invoices",
    "suppliers": "vendor_master",
    "corporate_vendors": "vendor_master",
    "purchase_orders": "purchase_orders",
    "purchase_order_lines": "purchase_order_lines",
    "inventory_balances": "inventory",
    "supplier_invoices": "vendor_invoices",
    "ap_vendor_invoices_indirect": "vendor_invoices",
    # both
    "software_subscriptions": "subscriptions",
}
# Parents must land before children so lookups (customer -> policy, vendor -> PO -> line) resolve.
DATASET_ORDER = [
    "customers",
    "vendor_master",
    "invoices",
    "policies",
    "purchase_orders",
    "purchase_order_lines",
    "inventory",
    "vendor_invoices",
    "subscriptions",
]
MIME = {"csv": "text/csv", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def sid(key: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, key)


def schema_for(slug: str) -> str:
    return f"t_{sid(f'schema:{slug}').hex[:12]}"


def upsert(session: Session, model, id_: uuid.UUID, **values):
    """Insert or update one row by primary key. Updates go through a column-checked
    UPDATE statement so a typo in a keyword is an error, not a silent no-op."""
    columns = model.__table__.columns.keys()
    if unknown := sorted(set(values) - set(columns)):
        raise AttributeError(f"{model.__name__} has no column(s) {unknown}")
    row = session.get(model, id_)
    if row is None:
        row = model(id=id_, **values)
        session.add(row)
        session.flush()
        return row
    session.execute(update(model).where(model.id == id_).values(**{k: v for k, v in values.items() if k != "id"}))
    session.refresh(row)
    return row


# ---- Platform: firm, analyst, six companies ---------------------------------------------


def seed_firm(session: Session, analyst_key: str | None) -> tuple[Firm, User]:
    home = upsert(session, Tenant, sid("tenant:firm"), name=FIRM["name"], schema_name=schema_for("firm"))
    session.flush()
    user = None
    if analyst_key:
        user = session.scalar(select(User).where(User.api_token_hash == token_digest(analyst_key)))
    if user is None:
        user = session.scalar(select(User).where(User.email == ANALYST["email"]).order_by(User.created_at))
    if user is None:
        if not analyst_key:
            raise SystemExit(f"No user {ANALYST['email']!r} exists yet; pass --analyst-key to create one.")
        user = User(id=sid("user:analyst"), tenant_id=home.id, email=ANALYST["email"], role="member")
        session.add(user)
    if analyst_key:
        user.api_token_hash = token_digest(analyst_key)
    session.flush()
    firm = upsert(session, Firm, sid("firm"), name=FIRM["name"], slug=FIRM["slug"], home_tenant_id=home.id, synthetic_demo=True)
    session.flush()
    upsert(
        session,
        FirmMembership,
        sid(f"membership:{user.id}"),
        firm_id=firm.id,
        user_id=user.id,
        role=ANALYST["role"],
        display_name=ANALYST["name"],
    )
    return firm, user


def retire_firm(session: Session, slug: str) -> bool:
    """Delete another firm (its firm_companies, memberships, opportunities and activity
    cascade) so its analyst signs into this one instead. Company tenants and their
    schemas are kept: jobs and users may still reference them."""
    firm = session.scalar(select(Firm).where(Firm.slug == slug))
    if firm is None:
        return False
    session.delete(firm)
    session.flush()
    return True


def company_dirs(manifest: dict) -> list[tuple[str, dict, Path]]:
    out = []
    for sector, companies in manifest["sectors"].items():
        for c in companies:
            out.append((sector, c, SYNTHETIC / sector / c["slug"]))
    return out


def seed_company(session: Session, firm: Firm, sector: str, c: dict, root: Path) -> FirmCompany:
    """Upsert the firm company. A company that already exists keeps the tenant it is linked
    to (`link-workspace` may have pointed it at the employees' workspace); only a new company
    gets the loader's own deterministic tenant."""
    profile = json.loads((root / "00_company" / "company_profile.json").read_text())
    existing = session.get(FirmCompany, sid(f"company:{c['slug']}"))
    if existing is not None and existing.tenant_id is not None:
        tenant = session.get(Tenant, existing.tenant_id)
    else:
        tenant = upsert(session, Tenant, sid(f"tenant:{c['slug']}"), name=c["name"], schema_name=schema_for(c["slug"]))
    session.flush()
    industry = profile.get("industry") or sector.replace("_", " ").title()
    short = c["slug"].split("_")[0]
    hq = profile.get("headquarters") or profile.get("hq", "")
    if isinstance(hq, dict):
        hq = ", ".join(str(v) for v in (hq.get("city"), hq.get("state")) if v)
    return upsert(
        session,
        FirmCompany,
        sid(f"company:{c['slug']}"),
        firm_id=firm.id,
        tenant_id=tenant.id,
        slug=short,
        name=c["name"],
        location=hq,
        industry=industry,
        description=profile.get("description", ""),
        acquisition_date=date.fromisoformat(ACQUIRED[short]) if short in ACQUIRED else None,
        status="active",
        synthetic_demo=True,
    )


# ---- Fact layer: every canonical table through the import pipeline -----------------------


def source_tables(root: Path) -> list[tuple[Path, str | None, str]]:
    """(file, sheet, dataset) for every CSV / workbook sheet with a canonical home."""
    out: list[tuple[Path, str | None, str]] = []
    for path in sorted(root.rglob("*")):
        if path.suffix == ".csv" and path.stem in DATASET_BY_TABLE:
            out.append((path, None, DATASET_BY_TABLE[path.stem]))
        elif path.suffix == ".xlsx":
            for sheet in workbook_sheets(path.read_bytes()):
                if sheet in DATASET_BY_TABLE:
                    out.append((path, sheet, DATASET_BY_TABLE[sheet]))
    out.sort(key=lambda t: (DATASET_ORDER.index(t[2]), str(t[0]), t[1] or ""))
    return out


def existing_job_status(company: CompanyRef, content: bytes, sheet: str | None, dataset: str) -> str | None:
    """Status of the most recent non-failed import job for this exact file content +
    sheet + dataset in the company tenant, or None if it has never been imported."""
    digest = hashlib.sha256(content).hexdigest()
    with tenant_session(company.schema) as ts:
        q = (
            select(ImportJob.status)
            .join(SourceFile, SourceFile.id == ImportJob.source_file_id)
            .where(
                ImportJob.company_id == company.id,
                ImportJob.status != "failed",
                ImportJob.dataset_type == dataset,
                SourceFile.content_hash == digest,
            )
            .order_by(ImportJob.created_at.desc())
        )
        if sheet:
            q = q.where(ImportJob.sheet_name == sheet)
        return ts.scalar(q)


def mapping_decisions(company: CompanyRef, job_id: uuid.UUID, accept_model: bool) -> list[dict]:
    """The loader stands in for the analyst at the mapping gate: deterministic
    matches at/above AUTO_CONFIDENCE are confirmed; weak header guesses are left
    unmapped so no column lands on a wrong field silently. Model proposals (capped
    just under the threshold by the agent processor) are confirmed only with
    --accept-model-mappings; otherwise they stay `needs_review` in field_mappings."""
    decisions = []
    with tenant_session(company.schema) as ts:
        for m in ts.scalars(select(FieldMapping).where(FieldMapping.import_job_id == job_id)):
            accept = bool(m.target_field) and (m.confidence >= AUTO_CONFIDENCE or (accept_model and m.reason.startswith("model:")))
            decisions.append({"source": m.source_column, "target": m.target_field if accept else None, "confirmed": accept})
    return decisions


def import_company(platform: Session, ctx: FirmContext, company: CompanyRef, root: Path, processor, accept_model: bool) -> dict:
    report: dict = {"imported": [], "skipped_existing": [], "awaiting_mapping_review": [], "exceptions_open": 0, "rows": 0}
    for path, sheet, dataset in source_tables(root):
        content = path.read_bytes()
        label = f"{path.relative_to(root)}" + (f"::{sheet}" if sheet else "")
        status = existing_job_status(company, content, sheet, dataset)
        if status == "completed":
            report["skipped_existing"].append(label)
            continue
        if status is not None:
            report["awaiting_mapping_review"].append({"table": label, "reason": f"import job already open ({status})"})
            continue
        job = imports.create_import(
            platform, ctx, company, path.name, content, MIME[path.suffix[1:]], processor=processor, dataset=dataset, sheet=sheet
        )
        try:
            job = imports.approve_mappings(
                platform, ctx, company, job.id, mapping_decisions(company, job.id, accept_model), processor=processor
            )
        except HTTPException as exc:
            # A required field has no confident mapping (e.g. a mislabelled header). The
            # job stays at the mapping gate for the analyst; nothing is guessed.
            report["awaiting_mapping_review"].append({"table": label, "job_id": str(job.id), "reason": exc.detail})
            continue
        job = imports.approve_import(platform, ctx, company, job.id)
        report["imported"].append({"table": label, "dataset": dataset, "rows": job.records_imported, "review": job.records_needing_review})
        report["rows"] += job.records_imported
        report["exceptions_open"] += job.records_needing_review
    return report


def db_rows(schema: str) -> dict[str, int]:
    with tenant_session(schema) as ts:
        return {
            m.__tablename__: ts.scalar(select(func.count()).select_from(m)) or 0
            for m in (Customer, Invoice, Vendor, VendorPurchase, Subscription, Policy, PurchaseOrder, PurchaseOrderLine, InventoryBalance)
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--analyst-key", default=settings.demo_analyst_key or None, help="64-hex analyst access key")
    parser.add_argument("--only", action="append", default=[], help="company slug prefix to load (repeatable)")
    parser.add_argument("--no-analyze", action="store_true", help="fact layer only; skip the interpretation agents")
    parser.add_argument("--processor", default=None, help="import processor name (demo|agent); default from settings")
    parser.add_argument(
        "--accept-model-mappings", action="store_true", help="confirm model-proposed column mappings instead of leaving them for review"
    )
    parser.add_argument(
        "--replace-firm",
        action="append",
        default=[],
        metavar="SLUG",
        help="delete this firm first (e.g. the northstar HVAC demo); repeatable",
    )
    args = parser.parse_args()
    if args.analyst_key and not (len(args.analyst_key) == 64 and all(ch in "0123456789abcdef" for ch in args.analyst_key.lower())):
        parser.error("--analyst-key must be 64 hex characters")

    manifest = json.loads(MANIFEST.read_text())
    companies = [(s, c, r) for s, c, r in company_dirs(manifest) if not args.only or any(c["slug"].startswith(o) for o in args.only)]

    migrate_platform()
    ensure_bucket()
    with platform_session() as session:
        retired = [slug for slug in args.replace_firm if retire_firm(session, slug)]
        firm, user = seed_firm(session, args.analyst_key.lower() if args.analyst_key else None)
        for sector, c, root in companies:
            seed_company(session, firm, sector, c, root)
        session.commit()
        home = session.get(Tenant, firm.home_tenant_id)
        principal = Principal(user_id=user.id, tenant_id=home.id, tenant_schema=home.schema_name, email=user.email, role="member")

    migrate_tenant_schema(schema_for("firm"))
    with platform_session() as platform:
        # Migrate whichever tenant each company is linked to (the loader's own, or a
        # workspace linked with `link-workspace`) and make sure it carries the one Deal
        # the employee-facing surfaces scope by.
        for _sector, c, _root in companies:
            fc = platform.get(FirmCompany, sid(f"company:{c['slug']}"))
            schema = platform.get(Tenant, fc.tenant_id).schema_name
            migrate_tenant_schema(schema)
            ensure_company_deal(platform, fc, schema, user.id)
        platform.commit()

    processor = get_processor(args.processor)
    report = {"firm": FIRM["name"], "replaced_firms": retired, "as_of": manifest.get("as_of_date"), "companies": {}}
    with platform_session() as platform:
        ctx = load_firm_context(platform, principal)
        for _sector, c, root in companies:
            company = ctx.company(sid(f"company:{c['slug']}"))
            counts = import_company(platform, ctx, company, root, processor, args.accept_model_mappings)
            counts["db_rows"] = db_rows(company.schema)
            report["companies"][c["name"]] = counts
        platform.commit()
        if not args.no_analyze:
            report["interpretation"] = run_portfolio_interpretation(platform, ctx)
            platform.commit()
    if not args.no_analyze:
        # The loader doubles as the worker: drain the queue through the same code path
        # `python -m vista.jobs.worker` uses, then report each job's final status.
        while process_one():
            pass
        with platform_session() as platform:
            # Merge jobs are created by the sector barrier once every review is terminal, so the
            # queue-time report has None for them; resolve them by their stable keys now.
            ctx = load_firm_context(platform, principal)
            request_id = report["interpretation"]["request_id"]
            for sector in report["interpretation"]["merge"]:
                job = release_sector_barrier(platform, ctx, request_id, sector)
                report["interpretation"]["merge"][sector] = str(job.id) if job is not None else None
            platform.commit()
        while process_one():
            pass
        with platform_session() as platform:
            for hop in ("review", "merge"):
                group = report["interpretation"][hop]
                for key, job_id in group.items():
                    job = platform.get(Job, uuid.UUID(job_id)) if job_id else None
                    group[key] = {"job_id": job_id, "status": job.status if job else "waiting", "error": job.error if job else None}
    print(json.dumps({"loaded_at": datetime.now(UTC).isoformat(timespec="seconds"), **report}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
