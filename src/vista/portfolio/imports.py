"""Placeholder import pipeline: upload → inspect → mappings → normalise →
exceptions → approve → canonical rows + provenance. Depends only on the
ImportProcessor contract, so swapping the processor leaves the API intact."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

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
    InventoryBalance,
    Invoice,
    Policy,
    PurchaseOrder,
    PurchaseOrderLine,
    RecordProvenance,
    SourceFile,
    Subscription,
    Vendor,
    VendorPurchase,
)
from vista.portfolio import serializers as ser
from vista.portfolio.access import CompanyRef, FirmContext, company_session, today
from vista.portfolio.processors import DATASETS, UNREADABLE_AMOUNT, ImportProcessor, ProposedMapping, get_processor
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
    dataset: str | None = None,
    sheet: str | None = None,
) -> ImportJob:
    """Stores the raw file, stages every source row as an ImportRecord and
    proposes field mappings. Nothing canonical is written yet. `dataset`
    overrides detection (the caller knows what the file is); `sheet` picks a
    worksheet other than the first in a workbook."""
    if len(content) > MAX_UPLOAD:
        raise HTTPException(413, "File exceeds the 25 MB import limit")
    if dataset is not None and dataset not in DATASETS:
        raise HTTPException(422, f"Unknown dataset type {dataset!r}")
    proc = processor or get_processor()
    inspection = proc.inspect_file(filename, content, sheet)
    if dataset is not None:
        inspection.dataset = dataset
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
                reason=m.reason or "",
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


def job_view(company: CompanyRef, job_id: uuid.UUID) -> dict:
    """The import job as the analyst wizard and the company workspace render it:
    file, detection, mappings, exceptions and the first raw rows."""
    with company_session(company) as ts:
        job = ts.get(ImportJob, job_id)
        if job is None:
            raise HTTPException(404, "Import job not found")
        file = ts.get(SourceFile, job.source_file_id)
        out = ser.import_job(job, file, job_mappings(ts, job))
        out["exceptions"] = [
            ser.import_exception(x)
            for x in ts.scalars(select(ImportException).where(ImportException.import_job_id == job.id).order_by(ImportException.ref))
        ]
        out["sample"] = _raw_rows(job, ts)[:5]
    return out


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
                    "normalized": "unreadable" if value is None else str(value),
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
                        "field": x.field_name,
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
    """Quantize a normalized number. Empty is zero; anything unreadable is an error,
    never a silent zero — normalization already turned such cells into exceptions."""
    if v in (None, ""):
        return Decimal(0).quantize(Decimal(places))
    try:
        return Decimal(str(v)).quantize(Decimal(places))
    except (InvalidOperation, ValueError, TypeError) as e:
        raise ValueError(f"unreadable amount {v!r}") from e


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
        swap_rows: set[int] = set()
        undecided_rows: set[int] = set()  # unreadable amounts nobody decided: rejected, never zeroed
        zeroed: set[tuple[int, str]] = set()  # (row, field) the analyst chose to import as zero
        vendor_alias: dict[str, str] = {}
        for x in exceptions:
            d = x.detail or {}
            if x.exception_type == UNREADABLE_AMOUNT and d.get("leftRow"):
                if x.resolution == "Skip row":
                    skip_rows.add(d["leftRow"])
                elif x.resolution == "Import as zero" and d.get("field"):
                    zeroed.add((d["leftRow"], d["field"]))
                else:
                    undecided_rows.add(d["leftRow"])
                continue
            if d.get("dataset") == "customers" and x.resolution == "Merge" and d.get("rightRow"):
                skip_rows.add(d["rightRow"])
            if d.get("dataset") == "invoices" and x.resolution == "Skip duplicate" and d.get("rightRow"):
                skip_rows.add(d["rightRow"])
            if d.get("dataset") == "vendors" and x.resolution == "Match" and d.get("matchVendor"):
                vendor_alias[d["left"]] = d["matchVendor"]
            if d.get("dataset") == "policies" and x.resolution == "Swap dates":
                swap_rows.update(d.get("recordRows") or [])
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
        lookups = Lookups.load(ts, vendor_alias)
        for rec in records:
            if rec.source_row in skip_rows:
                rec.status = "merged"
                rejected += 1
                continue
            if rec.source_row in undecided_rows:
                rec.status = "rejected"
                rec.exception_reason = UNREADABLE_AMOUNT
                rejected += 1
                continue
            normalized = dict(rec.normalized_record)
            if rec.source_row in swap_rows:
                normalized["effective_date"], normalized["expiration_date"] = (
                    normalized.get("expiration_date"),
                    normalized.get("effective_date"),
                )
                rec.normalized_record = normalized
            explicit = [f for row_no, f in zeroed if row_no == rec.source_row]
            if explicit:  # a working copy: the marker is for _write_canonical only, never stored
                normalized = {**normalized, **{f: 0 for f in explicit}, EXPLICIT_ZERO: explicit}
            row = _write_canonical(ts, job.dataset_type, normalized, base, lookups)
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


@dataclass
class Lookups:
    """In-schema indexes used to link imported rows to rows already in the ledger."""

    customers_by_name: dict[str, uuid.UUID] = field(default_factory=dict)
    customers_by_source: dict[str, uuid.UUID] = field(default_factory=dict)
    vendors_by_source: dict[str, Vendor] = field(default_factory=dict)
    vendors_by_id: dict[str, Vendor] = field(default_factory=dict)
    orders_by_number: dict[str, PurchaseOrder] = field(default_factory=dict)
    vendor_alias: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, ts: Session, vendor_alias: dict[str, str]) -> Lookups:
        lk = cls(vendor_alias=vendor_alias)
        for c in ts.scalars(select(Customer)):
            lk.customers_by_name.setdefault(c.display_name.lower(), c.id)
            if c.source_customer_id:
                lk.customers_by_source.setdefault(c.source_customer_id, c.id)
        for v in ts.scalars(select(Vendor)):
            lk.vendors_by_source.setdefault(v.source_name, v)
            if v.source_vendor_id:
                lk.vendors_by_id.setdefault(v.source_vendor_id, v)
        for po in ts.scalars(select(PurchaseOrder)):
            lk.orders_by_number.setdefault(po.po_number, po)
        return lk

    def customer(self, r: dict) -> uuid.UUID | None:
        source_id = str(r.get("source_customer_id") or "")
        name = str(r.get("customer_name") or "").lower()
        return self.customers_by_source.get(source_id) or self.customers_by_name.get(name)

    def vendor(self, ts: Session, r: dict, base: dict, provenance: bool = True) -> Vendor | None:
        """Existing vendor by source id or name, else a new one (with its own
        provenance row unless the caller writes one for the vendor itself)."""
        source = str(r.get("vendor_name") or "")
        source_id = str(r.get("source_vendor_id") or "")
        v = self.vendors_by_id.get(source_id) if source_id else None
        if v is None and source:
            v = self.vendors_by_source.get(source)
        if v is None and not source:
            return None
        if v is None:
            v = Vendor(
                **base,
                normalized_name=self.vendor_alias.get(source, source),
                source_name=source,
                source_vendor_id=source_id,
                category=str(r.get("category") or ""),
                payment_terms=str(r.get("payment_terms") or ""),
            )
            ts.add(v)
            ts.flush()
            self.vendors_by_source[source] = v
            if source_id:
                self.vendors_by_id[source_id] = v
            if not provenance:
                return v
            ts.add(
                RecordProvenance(
                    entity_type="vendor",
                    entity_id=v.id,
                    source_file_id=base["source_file_id"],
                    import_job_id=base["import_job_id"],
                    raw_value={"vendor": source, "vendor_id": source_id},
                    normalized_value={"normalized_name": v.normalized_name},
                    confidence=1.0,
                )
            )
        elif source_id and not v.source_vendor_id:
            v.source_vendor_id = source_id
            self.vendors_by_id[source_id] = v
        return v


EXPLICIT_ZERO = "_explicit_zero"  # fields the analyst resolved to zero; never derived, never persisted


def _money_or(r: dict, key: str, fallback: Decimal) -> Decimal:
    """The cell's value, or the derived fallback when the cell is empty or zero —
    unless the analyst decided that zero (an "Unreadable amount" imported as zero)."""
    if key in r.get(EXPLICIT_ZERO, ()):
        return _dec(0)
    return _dec(r.get(key)) if r.get(key) not in (None, "", 0) else fallback


def _write_canonical(ts: Session, dataset: str, r: dict, base: dict, lk: Lookups):
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
        lk.customers_by_name.setdefault(name.lower(), c.id)
        if c.source_customer_id:
            lk.customers_by_source.setdefault(c.source_customer_id, c.id)
        return c
    if dataset == "invoices":
        number = str(r.get("source_invoice_number") or "")
        customer_id = lk.customer(r)
        name = r.get("customer_name") or ""
        if not name and customer_id is not None:
            linked = ts.get(Customer, customer_id)
            name = linked.display_name if linked else ""
        if not number or not name:
            return None
        outstanding = _dec(r.get("outstanding_balance"))
        amount = _money_or(r, "amount", outstanding)
        due = _date(r.get("due_date"))
        source_status = str(r.get("status") or "")
        if source_status in ("cancelled", "void"):
            status = "void"
        elif source_status == "disputed":
            status = "disputed"
        else:
            status = "paid" if outstanding == 0 else ("overdue" if due and due < today() else "open")
        inv = Invoice(
            **base,
            customer_id=customer_id,
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
    if dataset == "vendor_master":
        v = lk.vendor(ts, r, base, provenance=False)
        if v is None:
            return None
        v.contact_name = v.contact_name or str(r.get("contact") or "")
        v.email = v.email or str(r.get("email") or "")
        v.phone = v.phone or str(r.get("phone") or "")
        v.category = v.category or str(r.get("category") or "")
        v.payment_terms = v.payment_terms or str(r.get("payment_terms") or "")
        if r.get("status") == "inactive":
            v.status = "inactive"
        return v
    if dataset in ("vendors", "vendor_invoices"):
        v = lk.vendor(ts, r, base)
        if v is None:
            return None
        if dataset == "vendors":
            qty = _dec(r.get("quantity"), "0.001")
            price = _dec(r.get("unit_price"), "0.0001")
            total = _money_or(r, "total", _dec(qty * price))
            sku, description, unit = r.get("sku") or "", r.get("description") or "", r.get("unit") or ""
        else:
            qty, price = Decimal("1.000"), _dec(r.get("total"), "0.0001")
            total = _dec(r.get("total"))
            number = r.get("invoice_number") or ""
            description = " · ".join(p for p in (r.get("category") or "", f"invoice {number}" if number else "") if p)
            sku, unit = "", ""
        p = VendorPurchase(
            **base,
            vendor_id=v.id,
            purchase_date=_date(r.get("date")),
            sku=sku,
            item_description=description,
            quantity=qty,
            unit=unit,
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
        annual = _dec(r.get("annual_cost"))
        if monthly == 0 and annual:
            monthly = _dec(annual / 12)
        if annual == 0:
            annual = monthly * 12
        s = Subscription(
            **base,
            vendor_name=r.get("vendor") or product,
            product_name=product,
            category=r.get("category") or "",
            monthly_cost=monthly,
            annual_cost=annual,
            seat_count=int(r.get("seats") or 0),
            renewal_date=_date(r.get("renewal_date")),
            restrictions_notes=r.get("notes") or "",
        )
        ts.add(s)
        return s
    if dataset == "policies":
        number = str(r.get("policy_number") or "")
        if not number:
            return None
        premium = _dec(r.get("annual_premium"))
        pct = _dec(r.get("commission_pct"))
        expected = _money_or(r, "expected_commission", _dec(premium * pct / 100))
        pol = Policy(
            **base,
            customer_id=lk.customer(r),
            customer_name=r.get("customer_name") or "",
            source_policy_id=str(r.get("source_policy_id") or ""),
            source_customer_id=str(r.get("source_customer_id") or ""),
            policy_number=number,
            line_of_business=str(r.get("line_of_business") or "")[:32],
            line_description=str(r.get("line_description") or "")[:128],
            carrier_code=str(r.get("carrier_code") or "")[:32],
            carrier_name=r.get("carrier_name") or "",
            effective_date=_date(r.get("effective_date")),
            expiration_date=_date(r.get("expiration_date")),
            term_months=int(r.get("term_months") or 12),
            annual_premium=premium,
            commission_pct=pct,
            expected_commission=expected,
            billing_type=str(r.get("billing_type") or "").lower().replace(" ", "_")[:32],
            status=str(r.get("status") or "in_force")[:32],
            producer_id=str(r.get("producer_id") or ""),
            account_manager_id=str(r.get("account_manager_id") or ""),
            surplus_lines=bool(r.get("surplus_lines")),
            new_or_renewal=str(r.get("new_or_renewal") or "").lower()[:16],
            experience_mod=_dec(r["experience_mod"], "0.001") if r.get("experience_mod") not in (None, "", 0) else None,
            umbrella_limit=_dec(r["umbrella_limit"]) if r.get("umbrella_limit") not in (None, "", 0) else None,
        )
        ts.add(pol)
        return pol
    if dataset == "purchase_orders":
        number = str(r.get("po_number") or "")
        if not number:
            return None
        v = lk.vendor(ts, r, base)
        po = PurchaseOrder(
            **base,
            vendor_id=v.id if v else None,
            po_number=number,
            source_supplier_id=str(r.get("source_vendor_id") or ""),
            supplier_name=r.get("vendor_name") or "",
            po_date=_date(r.get("date")),
            buyer_id=str(r.get("buyer_id") or ""),
            payment_terms=str(r.get("payment_terms") or ""),
            ship_via=str(r.get("ship_via") or ""),
            freight_terms=str(r.get("freight_terms") or ""),
            total_amount=_dec(r.get("total")),
            status=str(r.get("status") or "open")[:32],
            approved_by=str(r.get("approved_by") or ""),
            sent_method=str(r.get("sent_method") or ""),
        )
        ts.add(po)
        ts.flush()
        lk.orders_by_number.setdefault(number, po)
        return po
    if dataset == "purchase_order_lines":
        number = str(r.get("po_number") or "")
        if not number:
            return None
        qty = _dec(r.get("quantity"), "0.001")
        cost = _dec(r.get("unit_price"), "0.0001")
        po = lk.orders_by_number.get(number)
        line = PurchaseOrderLine(
            **base,
            purchase_order_id=po.id if po else None,
            po_number=number,
            line_number=int(r.get("line_number") or 0),
            item_id=str(r.get("item_id") or ""),
            description=r.get("description") or "",
            manufacturer_part_number=str(r.get("manufacturer_part_number") or ""),
            ordered_qty=qty,
            uom=str(r.get("unit") or ""),
            unit_cost=cost,
            extended_cost=_money_or(r, "total", _dec(qty * cost)),
            need_by_date=_date(r.get("need_by_date")),
            promised_date=_date(r.get("promised_date")),
            received_qty=_dec(r.get("received_qty"), "0.001"),
            status=str(r.get("status") or "open")[:32],
            gl_account=str(r.get("gl_account") or ""),
        )
        ts.add(line)
        return line
    if dataset == "inventory":
        item = str(r.get("item_id") or "")
        if not item:
            return None
        on_hand = _dec(r.get("on_hand_qty"), "0.001")
        cost = _dec(r.get("unit_price"), "0.0001")
        bal = InventoryBalance(
            **base,
            item_id=item,
            warehouse=str(r.get("warehouse") or ""),
            bin_location=str(r.get("bin_location") or ""),
            on_hand_qty=on_hand,
            allocated_qty=_dec(r.get("allocated_qty"), "0.001"),
            available_qty=_dec(r.get("available_qty"), "0.001"),
            on_order_qty=_dec(r.get("on_order_qty"), "0.001"),
            uom=str(r.get("unit") or ""),
            unit_cost=cost,
            extended_value=_money_or(r, "total", _dec(on_hand * cost)),
            last_count_date=_date(r.get("last_count_date")),
            last_receipt_date=_date(r.get("last_receipt_date")),
            last_issue_date=_date(r.get("last_issue_date")),
            as_of_date=_date(r.get("as_of_date")),
        )
        ts.add(bal)
        return bal
    return None
