"""Placeholder import pipeline: upload → inspect → mappings → normalise →
exceptions → approve → canonical rows + provenance. Depends only on the
ImportProcessor contract, so swapping the processor leaves the API intact."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vista.config import settings
from vista.models.platform import FirmCompany
from vista.models.tenant import (
    Customer,
    FieldMapping,
    ImportException,
    ImportJob,
    ImportRecord,
    Invoice,
    RecordProvenance,
    SourceFile,
    Subscription,
    Vendor,
    VendorPurchase,
)
from vista.portfolio.access import CompanyRef, FirmContext, company_session, today
from vista.portfolio.processors import DATASETS, ImportProcessor, ProposedMapping, get_processor
from vista.portfolio.service import log_activity
from vista.storage import s3_client

MAX_UPLOAD = 6 * 1024 * 1024
SOURCE_TYPE_BY_EXT = {"csv": "csv_import", "txt": "csv_import", "xlsx": "xlsx_import", "xlsm": "xlsx_import"}


def _now() -> datetime:
    return datetime.now(UTC)


def _store_file(company: CompanyRef, filename: str, content: bytes, file_id: uuid.UUID) -> str:
    key = f"portfolio/{company.id}/source/{file_id}/{filename}"
    try:
        s3_client().put_object(Bucket=settings.s3_bucket, Key=key, Body=content)
    except (ClientError, BotoCoreError) as exc:
        raise HTTPException(503, "Source storage is unavailable. The file was not saved; try again.") from exc
    return key


def _existing_vendor_names(ctx: FirmContext) -> list[str]:
    names: set[str] = set()
    for company in ctx.companies:
        with company_session(company) as ts:
            names.update(ts.scalars(select(Vendor.normalized_name)))
    return sorted(names)


def _raw_rows(job: ImportJob, ts: Session) -> list[dict]:
    return [
        r.raw_record for r in ts.scalars(select(ImportRecord).where(ImportRecord.import_job_id == job.id).order_by(ImportRecord.source_row))
    ]


def create_import(
    platform: Session,
    ctx: FirmContext,
    company: CompanyRef,
    filename: str,
    content: bytes,
    mime_type: str,
    processor: ImportProcessor | None = None,
) -> ImportJob:
    """Stores the raw file, stages every source row as an ImportRecord and
    proposes field mappings. Nothing canonical is written yet."""
    if len(content) > MAX_UPLOAD:
        raise HTTPException(413, "File exceeds the 25 MB import limit")
    proc = processor or get_processor()
    inspection = proc.inspect_file(filename, content)
    mappings = proc.propose_mappings(inspection.dataset, inspection.columns, inspection.rows)
    with company_session(company) as ts:
        file = SourceFile(
            company_id=company.id,
            filename=filename,
            mime_type=mime_type or "",
            file_size=len(content),
            content_hash=hashlib.sha256(content).hexdigest(),
            uploaded_by=ctx.principal.user_id,
        )
        ts.add(file)
        ts.flush()
        file.storage_key = _store_file(company, filename, content, file.id)
        job = ImportJob(
            company_id=company.id,
            source_file_id=file.id,
            status="mapping_review",
            dataset_type=inspection.dataset,
            detection_confidence=inspection.confidence,
            sheet_name=inspection.sheet,
            columns=inspection.columns,
            processor=proc.name,
            records_detected=len(inspection.rows),
            created_by=ctx.principal.user_id,
            started_at=_now(),
        )
        ts.add(job)
        ts.flush()
        _write_mappings(ts, job, mappings)
        for i, row in enumerate(inspection.rows):
            ts.add(
                ImportRecord(import_job_id=job.id, source_row=i + 2, raw_record=row, normalized_record={}, status="staged", confidence=0.0)
            )
        ts.commit()
    log_activity(
        platform,
        ctx.firm.id,
        company.id,
        f"{filename} uploaded for {company.name}: {len(inspection.rows)} rows detected as {DATASETS[inspection.dataset]['label'].lower()}.",
        "import",
    )
    platform.commit()
    return job


def _write_mappings(ts: Session, job: ImportJob, mappings: list[ProposedMapping]) -> None:
    for m in ts.scalars(select(FieldMapping).where(FieldMapping.import_job_id == job.id)):
        ts.delete(m)
    ts.flush()
    entity = DATASETS[job.dataset_type]["entity"] or "none"
    for m in mappings:
        ts.add(
            FieldMapping(
                import_job_id=job.id,
                source_column=m.source,
                example_value=str(m.example)[:500],
                target_entity=entity,
                target_field=m.target,
                confidence=m.confidence,
                status="needs_review" if m.needs_review else "proposed",
            )
        )


def set_dataset(
    ctx: FirmContext, company: CompanyRef, job_id: uuid.UUID, dataset: str, processor: ImportProcessor | None = None
) -> ImportJob:
    if dataset not in DATASETS:
        raise HTTPException(422, f"Unknown dataset type {dataset!r}")
    proc = processor or get_processor()
    with company_session(company) as ts:
        job = _job(ts, job_id)
        if job.status not in ("uploaded", "mapping_review", "validating", "ready_to_import"):
            raise HTTPException(409, f"Import is {job.status}; the dataset can no longer change")
        job.dataset_type = dataset
        job.status = "mapping_review"
        _write_mappings(ts, job, proc.propose_mappings(dataset, job.columns, _raw_rows(job, ts)))
        ts.commit()
    return job


def _job(ts: Session, job_id: uuid.UUID) -> ImportJob:
    job = ts.get(ImportJob, job_id)
    if job is None:
        raise HTTPException(404, "Import job not found")
    return job


def find_job(ctx: FirmContext, job_id: uuid.UUID) -> tuple[CompanyRef, ImportJob]:
    """Import jobs live in the company's schema, so resolve which company owns it (firm-scoped)."""
    for company in ctx.companies:
        with company_session(company) as ts:
            job = ts.get(ImportJob, job_id)
            if job is not None:
                ts.expunge(job)
                return company, job
    raise HTTPException(404, "Import job not found")


def preview(
    ctx: FirmContext, company: CompanyRef, job_id: uuid.UUID, processor: ImportProcessor | None = None, limit: int = 10
) -> list[dict]:
    proc = processor or get_processor()
    with company_session(company) as ts:
        job = _job(ts, job_id)
        mappings = _current_mappings(ts, job)
        rows = _raw_rows(job, ts)[:60]
    out: list[dict] = []
    seen: set[str] = set()
    for raw, rec in zip(rows, proc.normalize_records(job.dataset_type, rows, mappings), strict=False):
        for m in mappings:
            if not m.target or m.source not in raw:
                continue
            value = rec.normalized.get(m.target)
            if str(raw[m.source]) == str(value):
                continue
            key = f"{m.target}:{raw[m.source]}"
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "field": m.target,
                    "label": DATASETS[job.dataset_type]["fields"].get(m.target, {}).get("label", m.target),
                    "source": raw[m.source],
                    "normalized": str(value),
                }
            )
            if len(out) >= limit:
                return out
    return out


def job_mappings(ts: Session, job: ImportJob) -> list[FieldMapping]:
    """Field mappings in source-column order."""
    order = {c: i for i, c in enumerate(job.columns or [])}
    rows = ts.scalars(select(FieldMapping).where(FieldMapping.import_job_id == job.id)).all()
    return sorted(rows, key=lambda m: order.get(m.source_column, len(order)))


def _current_mappings(ts: Session, job: ImportJob) -> list[ProposedMapping]:
    return [
        ProposedMapping(m.source_column, m.example_value, m.target_field if m.status != "ignored" else None, m.confidence)
        for m in job_mappings(ts, job)
    ]


def approve_mappings(
    platform: Session,
    ctx: FirmContext,
    company: CompanyRef,
    job_id: uuid.UUID,
    decisions: list[dict],
    processor: ImportProcessor | None = None,
) -> ImportJob:
    """Applies the analyst's column decisions, normalises every staged row and
    records exceptions. Moves the job to `validating` (exceptions open) or
    `ready_to_import`."""
    proc = processor or get_processor()
    with company_session(company) as ts:
        job = _job(ts, job_id)
        if job.status not in ("uploaded", "mapping_review", "validating", "ready_to_import"):
            raise HTTPException(409, f"Import is {job.status}; mappings are locked")
        fields = DATASETS[job.dataset_type]["fields"]
        by_source = {m.source_column: m for m in ts.scalars(select(FieldMapping).where(FieldMapping.import_job_id == job.id))}
        chosen: set[str] = set()
        for d in decisions:
            m = by_source.get(d["source"])
            if m is None:
                raise HTTPException(422, f"Unknown source column {d['source']!r}")
            target = d.get("target") or None
            if target and target not in fields:
                raise HTTPException(422, f"{target!r} is not a field of {job.dataset_type}")
            if target and target in chosen:
                raise HTTPException(422, f"Two columns map to {target!r}")
            if target:
                chosen.add(target)
            if d.get("confirmed") or target != m.target_field:
                m.confidence = 1.0 if target else 0.0
            m.target_field = target
            m.status = "approved" if target else "ignored"
            m.approved_by, m.approved_at = ctx.principal.user_id, _now()
        for m in by_source.values():
            if m.status in ("proposed", "needs_review"):
                if m.target_field and m.confidence >= 0.9:
                    m.status, m.approved_by, m.approved_at = "approved", ctx.principal.user_id, _now()
                elif m.target_field:
                    raise HTTPException(422, f"Column {m.source_column!r} still needs a decision")
                else:
                    m.status = "ignored"
        for key, d in fields.items():
            if d.get("required") and key not in {m.target_field for m in by_source.values() if m.status == "approved"}:
                raise HTTPException(422, f"Required field '{d['label']}' is not mapped")
        mappings = _current_mappings(ts, job)
        records = {r.source_row: r for r in ts.scalars(select(ImportRecord).where(ImportRecord.import_job_id == job.id))}
        normalized = proc.normalize_records(job.dataset_type, [records[k].raw_record for k in sorted(records)], mappings)
        for rec in normalized:
            row = records[rec.source_row]
            row.normalized_record, row.confidence, row.status, row.exception_reason = rec.normalized, rec.confidence, "staged", None
        for x in ts.scalars(select(ImportException).where(ImportException.import_job_id == job.id, ImportException.status == "open")):
            ts.delete(x)
        ts.flush()
        exceptions = proc.identify_exceptions(job.dataset_type, normalized, _existing_vendor_names(ctx))
        offset = ts.scalar(select(func.count()).select_from(ImportException).where(ImportException.import_job_id == job.id)) or 0
        for i, x in enumerate(exceptions):
            rec = records.get(x.left_row) if x.left_row else None
            ts.add(
                ImportException(
                    import_job_id=job.id,
                    import_record_id=rec.id if rec else None,
                    ref=f"X-{offset + i + 1}",
                    exception_type=x.exception_type,
                    description=x.description,
                    candidate_matches=x.candidates,
                    detail={
                        "left": x.left,
                        "right": x.right,
                        "leftRow": x.left_row,
                        "rightRow": x.right_row,
                        "dataset": x.dataset,
                        "matchVendor": x.match_vendor,
                        "actions": x.actions,
                        "recordRows": x.record_rows,
                    },
                    confidence=x.confidence,
                )
            )
            if rec is not None:
                rec.exception_reason = x.exception_type
        job.records_needing_review = sum(1 for r in normalized if r.confidence < 0.9)
        job.status = "validating" if exceptions else "ready_to_import"
        ts.commit()
    log_activity(
        platform,
        ctx.firm.id,
        company.id,
        f"Field mappings approved for import {str(job.id)[:8]} "
        f"({len(records)} rows, {len(exceptions)} exception{'' if len(exceptions) == 1 else 's'}).",
        "import",
    )
    platform.commit()
    return job


def resolve_exception(
    platform: Session, ctx: FirmContext, company: CompanyRef, job_id: uuid.UUID, exception_ref: str, decision: str | None
) -> ImportException:
    with company_session(company) as ts:
        job = _job(ts, job_id)
        x = ts.scalar(select(ImportException).where(ImportException.import_job_id == job.id, ImportException.ref == exception_ref))
        if x is None:
            raise HTTPException(404, "Exception not found")
        detail = x.detail or {}
        if decision is None:
            x.status, x.resolution, x.resolved_by, x.resolved_at = "open", None, None, None
        else:
            if decision not in detail.get("actions", []):
                raise HTTPException(422, f"{decision!r} is not an allowed resolution")
            x.status, x.resolution, x.resolved_by, x.resolved_at = "resolved", decision, ctx.principal.user_id, _now()
            if job.status == "completed" and detail.get("dataset") == "vendors" and decision == "Match" and detail.get("matchVendor"):
                for v in ts.scalars(select(Vendor).where(Vendor.source_name == detail["left"])):
                    v.normalized_name = detail["matchVendor"]
        ts.commit()
    if decision is not None:
        log_activity(
            platform,
            ctx.firm.id,
            company.id,
            f"Import exception {x.ref} resolved: {decision} ({detail.get('left')} / {detail.get('right')}).",
            "import",
        )
        platform.commit()
    return x


# ---- Approval: staging → canonical -------------------------------------------------


def _dec(v, places: str = "0.01") -> Decimal:
    try:
        return Decimal(str(v or 0)).quantize(Decimal(places))
    except Exception:
        return Decimal(0)


def _date(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def approve_import(platform: Session, ctx: FirmContext, company: CompanyRef, job_id: uuid.UUID) -> ImportJob:
    """Writes canonical rows for every staged record (honouring exception
    decisions) with row-level provenance, then marks the job completed."""
    source_type = None
    with company_session(company) as ts:
        job = _job(ts, job_id)
        if job.status == "completed":
            return job
        if job.status not in ("validating", "ready_to_import"):
            raise HTTPException(409, f"Import is {job.status}; approve the field mappings first")
        file = ts.get(SourceFile, job.source_file_id)
        source_type = SOURCE_TYPE_BY_EXT.get(file.filename.rsplit(".", 1)[-1].lower(), "csv_import")
        records = ts.scalars(select(ImportRecord).where(ImportRecord.import_job_id == job.id).order_by(ImportRecord.source_row)).all()
        exceptions = ts.scalars(select(ImportException).where(ImportException.import_job_id == job.id)).all()
        skip_rows: set[int] = set()
        vendor_alias: dict[str, str] = {}
        for x in exceptions:
            d = x.detail or {}
            if d.get("dataset") == "customers" and x.resolution == "Merge" and d.get("rightRow"):
                skip_rows.add(d["rightRow"])
            if d.get("dataset") == "vendors" and x.resolution == "Match" and d.get("matchVendor"):
                vendor_alias[d["left"]] = d["matchVendor"]
        job.status, job.started_at = "importing", job.started_at or _now()
        ts.flush()
        base = {
            "company_id": company.id,
            "data_source_type": source_type,
            "source_file_id": file.id,
            "import_job_id": job.id,
            "synthetic_demo": False,
        }
        entity = DATASETS[job.dataset_type]["entity"]
        imported = 0
        rejected = 0
        customers_by_name = {c.display_name.lower(): c.id for c in ts.scalars(select(Customer))}
        vendors_by_source = {v.source_name: v for v in ts.scalars(select(Vendor))}
        for rec in records:
            if rec.source_row in skip_rows:
                rec.status = "merged"
                rejected += 1
                continue
            row = _write_canonical(ts, job.dataset_type, rec.normalized_record, base, customers_by_name, vendors_by_source, vendor_alias)
            if row is None:
                rec.status = "rejected"
                rec.exception_reason = rec.exception_reason or "missing required field"
                rejected += 1
                continue
            ts.flush()
            rec.status, rec.entity_id = "imported", row.id
            imported += 1
            ts.add(
                RecordProvenance(
                    entity_type=entity,
                    entity_id=row.id,
                    source_file_id=file.id,
                    source_filename=file.filename,
                    sheet_name=job.sheet_name,
                    row_number=rec.source_row,
                    raw_value=rec.raw_record,
                    normalized_value=rec.normalized_record,
                    import_job_id=job.id,
                    confidence=rec.confidence,
                    human_verified=rec.confidence < 0.9 or rec.exception_reason is not None,
                )
            )
        job.records_imported, job.records_rejected = imported, rejected
        job.status, job.completed_at = "completed", _now()
        ts.commit()
    fc = platform.get(FirmCompany, company.id)
    if fc.status == "onboarding":
        fc.status = "active"
    log_activity(
        platform,
        ctx.firm.id,
        company.id,
        f"{company.name} import completed: {imported} records accepted, {job.records_needing_review} reviewed, {rejected} rejected.",
        "import",
    )
    platform.commit()
    return job


def _write_canonical(ts: Session, dataset: str, r: dict, base: dict, customers_by_name: dict, vendors_by_source: dict, vendor_alias: dict):
    if dataset == "customers":
        name = r.get("customer_name") or ""
        if not name:
            return None
        c = Customer(
            **base,
            legal_name=name,
            display_name=name,
            source_customer_id=str(r.get("source_customer_id") or ""),
            contact_name=r.get("contact") or "",
            email=r.get("email") or "",
            phone=r.get("phone") or "",
            address_line_1=r.get("address") or "",
            city=r.get("city") or "",
            state=r.get("state") or "",
            postal_code=str(r.get("zip") or ""),
            service_type=r.get("service_type") or "",
            status="inactive" if r.get("status") == "inactive" else "active",
        )
        ts.add(c)
        ts.flush()
        customers_by_name.setdefault(name.lower(), c.id)
        return c
    if dataset == "invoices":
        number = r.get("source_invoice_number") or ""
        name = r.get("customer_name") or ""
        if not number or not name:
            return None
        outstanding = _dec(r.get("outstanding_balance"))
        amount = _dec(r.get("amount")) if r.get("amount") not in (None, "", 0) else outstanding
        due = _date(r.get("due_date"))
        status = "paid" if outstanding == 0 else ("overdue" if due and due < today() else "open")
        inv = Invoice(
            **base,
            customer_id=customers_by_name.get(name.lower()),
            customer_name=name,
            source_invoice_number=number,
            issue_date=_date(r.get("issue_date")),
            due_date=due,
            amount=amount,
            outstanding_balance=outstanding,
            status=status,
        )
        ts.add(inv)
        return inv
    if dataset == "vendors":
        source = r.get("vendor_name") or ""
        if not source:
            return None
        v = vendors_by_source.get(source)
        if v is None:
            v = Vendor(**base, normalized_name=vendor_alias.get(source, source), source_name=source)
            ts.add(v)
            ts.flush()
            vendors_by_source[source] = v
            ts.add(
                RecordProvenance(
                    entity_type="vendor",
                    entity_id=v.id,
                    source_file_id=base["source_file_id"],
                    import_job_id=base["import_job_id"],
                    raw_value={"vendor": source},
                    normalized_value={"normalized_name": v.normalized_name},
                    confidence=1.0,
                )
            )
        qty = _dec(r.get("quantity"), "0.001")
        price = _dec(r.get("unit_price"), "0.0001")
        total = _dec(r.get("total")) if r.get("total") not in (None, "", 0) else _dec(qty * price)
        p = VendorPurchase(
            **base,
            vendor_id=v.id,
            purchase_date=_date(r.get("date")),
            sku=r.get("sku") or "",
            item_description=r.get("description") or "",
            quantity=qty,
            unit=r.get("unit") or "",
            unit_price=price,
            total_amount=total,
        )
        ts.add(p)
        return p
    if dataset == "subscriptions":
        product = r.get("product") or ""
        if not product:
            return None
        monthly = _dec(r.get("monthly_cost"))
        s = Subscription(
            **base,
            vendor_name=product,
            product_name=product,
            category=r.get("category") or "",
            monthly_cost=monthly,
            annual_cost=monthly * 12,
            seat_count=int(r.get("seats") or 0),
            renewal_date=_date(r.get("renewal_date")),
            restrictions_notes=r.get("notes") or "",
        )
        ts.add(s)
        return s
    return None
