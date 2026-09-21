"""Row → JSON shapes consumed by the analyst frontend (camelCase, matching the
record shapes the UI already renders)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from vista.models.platform import FirmCompany, Opportunity, PortfolioActivity
from vista.models.tenant import (
    Customer,
    FieldMapping,
    ImportException,
    ImportJob,
    InventoryBalance,
    Invoice,
    Policy,
    PurchaseOrder,
    PurchaseOrderLine,
    RecordProvenance,
    SourceFile,
    Subscription,
    Task,
    Vendor,
    VendorPurchase,
    WorkspaceAgent,
    WorkspaceAgentRun,
    WorkspaceFinding,
)


def num(v) -> float | None:
    if v is None:
        return None
    return float(v) if isinstance(v, Decimal) else v


def iso(v: datetime | date | None) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo:
            v = v.astimezone(UTC)
        return v.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"
    return v.isoformat()


def sid(v: uuid.UUID | None) -> str | None:
    return str(v) if v is not None else None


def provenance(p: RecordProvenance | None, import_job_ref: str | None = None) -> dict | None:
    if p is None:
        return None
    return {
        "file": p.source_filename,
        "sheet": p.sheet_name,
        "row": p.row_number,
        "original": p.raw_value,
        "normalized": p.normalized_value,
        "importJob": import_job_ref or sid(p.import_job_id),
        "confidence": p.confidence,
        "review": "human-reviewed" if p.human_verified else ("auto-accepted" if p.confidence >= 0.9 else "reviewed"),
    }


def customer(c: Customer, prov: RecordProvenance | None = None) -> dict:
    return {
        "id": sid(c.id),
        "companyId": sid(c.company_id),
        "name": c.display_name,
        "legalName": c.legal_name,
        "sourceCustomerId": c.source_customer_id,
        "contact": c.contact_name,
        "email": c.email,
        "phone": c.phone,
        "address": c.address_line_1,
        "city": c.city,
        "state": c.state,
        "zip": c.postal_code,
        "serviceType": c.service_type,
        "status": c.status,
        "dataSourceType": c.data_source_type,
        "syntheticDemo": c.synthetic_demo,
        "provenance": provenance(prov),
    }


def invoice(i: Invoice, prov: RecordProvenance | None = None) -> dict:
    return {
        "id": sid(i.id),
        "companyId": sid(i.company_id),
        "customerId": sid(i.customer_id),
        "customerName": i.customer_name,
        "number": i.source_invoice_number,
        "issueDate": iso(i.issue_date),
        "dueDate": iso(i.due_date),
        "amount": num(i.amount),
        "outstanding": num(i.outstanding_balance),
        "status": i.status,
        "dataSourceType": i.data_source_type,
        "syntheticDemo": i.synthetic_demo,
        "provenance": provenance(prov),
    }


def vendor(v: Vendor, prov: RecordProvenance | None = None) -> dict:
    return {
        "id": sid(v.id),
        "companyId": sid(v.company_id),
        "name": v.normalized_name,
        "sourceName": v.source_name,
        "contact": v.contact_name or v.email,
        "status": v.status,
        "dataSourceType": v.data_source_type,
        "syntheticDemo": v.synthetic_demo,
        "provenance": provenance(prov),
    }


def purchase(p: VendorPurchase, vendor_name: str, prov: RecordProvenance | None = None) -> dict:
    return {
        "id": sid(p.id),
        "companyId": sid(p.company_id),
        "vendorId": sid(p.vendor_id),
        "vendorName": vendor_name,
        "sku": p.sku,
        "description": p.item_description,
        "quantity": num(p.quantity),
        "unit": p.unit,
        "unitPrice": num(p.unit_price),
        "date": iso(p.purchase_date),
        "total": num(p.total_amount),
        "dataSourceType": p.data_source_type,
        "syntheticDemo": p.synthetic_demo,
        "provenance": provenance(prov),
    }


def subscription(s: Subscription, prov: RecordProvenance | None = None) -> dict:
    return {
        "id": sid(s.id),
        "companyId": sid(s.company_id),
        "product": s.product_name,
        "vendorName": s.vendor_name,
        "category": s.category,
        "monthlyCost": num(s.monthly_cost),
        "annualCost": num(s.annual_cost),
        "seats": s.seat_count,
        "renewalDate": iso(s.renewal_date),
        "contractEndDate": iso(s.contract_end_date),
        "notes": s.restrictions_notes,
        "dataSourceType": s.data_source_type,
        "syntheticDemo": s.synthetic_demo,
        "provenance": provenance(prov),
    }


def policy(p: Policy, prov: RecordProvenance | None = None) -> dict:
    return {
        "id": sid(p.id),
        "companyId": sid(p.company_id),
        "customerId": sid(p.customer_id),
        "customerName": p.customer_name,
        "policyNumber": p.policy_number,
        "lineOfBusiness": p.line_of_business,
        "lineDescription": p.line_description,
        "carrier": p.carrier_name or p.carrier_code,
        "effectiveDate": iso(p.effective_date),
        "expirationDate": iso(p.expiration_date),
        "termMonths": p.term_months,
        "annualPremium": num(p.annual_premium),
        "commissionPct": num(p.commission_pct),
        "expectedCommission": num(p.expected_commission),
        "billingType": p.billing_type,
        "status": p.status,
        "newOrRenewal": p.new_or_renewal,
        "surplusLines": p.surplus_lines,
        "dataSourceType": p.data_source_type,
        "syntheticDemo": p.synthetic_demo,
        "provenance": provenance(prov),
    }


def purchase_order(po: PurchaseOrder, line_count: int, prov: RecordProvenance | None = None) -> dict:
    return {
        "id": sid(po.id),
        "companyId": sid(po.company_id),
        "vendorId": sid(po.vendor_id),
        "poNumber": po.po_number,
        "supplierName": po.supplier_name,
        "date": iso(po.po_date),
        "buyer": po.buyer_id,
        "paymentTerms": po.payment_terms,
        "shipVia": po.ship_via,
        "total": num(po.total_amount),
        "status": po.status,
        "lineCount": line_count,
        "dataSourceType": po.data_source_type,
        "syntheticDemo": po.synthetic_demo,
        "provenance": provenance(prov),
    }


def purchase_order_line(line: PurchaseOrderLine, prov: RecordProvenance | None = None) -> dict:
    return {
        "id": sid(line.id),
        "companyId": sid(line.company_id),
        "purchaseOrderId": sid(line.purchase_order_id),
        "poNumber": line.po_number,
        "lineNumber": line.line_number,
        "itemId": line.item_id,
        "description": line.description,
        "manufacturerPartNumber": line.manufacturer_part_number,
        "orderedQty": num(line.ordered_qty),
        "receivedQty": num(line.received_qty),
        "uom": line.uom,
        "unitCost": num(line.unit_cost),
        "extendedCost": num(line.extended_cost),
        "needByDate": iso(line.need_by_date),
        "promisedDate": iso(line.promised_date),
        "status": line.status,
        "dataSourceType": line.data_source_type,
        "syntheticDemo": line.synthetic_demo,
        "provenance": provenance(prov),
    }


def inventory_balance(b: InventoryBalance, prov: RecordProvenance | None = None) -> dict:
    return {
        "id": sid(b.id),
        "companyId": sid(b.company_id),
        "itemId": b.item_id,
        "warehouse": b.warehouse,
        "bin": b.bin_location,
        "onHandQty": num(b.on_hand_qty),
        "allocatedQty": num(b.allocated_qty),
        "availableQty": num(b.available_qty),
        "onOrderQty": num(b.on_order_qty),
        "uom": b.uom,
        "unitCost": num(b.unit_cost),
        "extendedValue": num(b.extended_value),
        "lastCountDate": iso(b.last_count_date),
        "lastReceiptDate": iso(b.last_receipt_date),
        "lastIssueDate": iso(b.last_issue_date),
        "asOfDate": iso(b.as_of_date),
        "dataSourceType": b.data_source_type,
        "syntheticDemo": b.synthetic_demo,
        "provenance": provenance(prov),
    }


def task(t: Task) -> dict:
    return {
        "id": t.ref,
        "uuid": sid(t.id),
        "companyId": sid(t.company_id),
        "title": t.title,
        "description": t.description,
        "category": t.category,
        "sourceType": t.source_type,
        "sourceId": t.source_id,
        "assignee": t.assignee,
        "priority": t.priority,
        "status": t.status,
        "dueDate": iso(t.due_date),
        "outcome": t.outcome,
        "outcomeNotes": t.outcome_notes,
        "realizedResult": num(t.realized_value),
        "createdBy": t.created_by,
        "createdAt": iso(t.created_at),
        "completedAt": iso(t.completed_at),
        "syntheticDemo": t.synthetic_demo,
    }


def opportunity(o: Opportunity) -> dict:
    return {
        "id": o.ref,
        "uuid": sid(o.id),
        "title": o.title,
        "category": o.category,
        "sku": o.sku,
        "companyIds": o.company_ids,
        "confidence": o.confidence,
        "status": o.status,
        "foundAt": iso(o.found_at),
        "fact": o.observed_fact,
        "evidence": o.evidence,
        "calculation": o.calculation,
        "benefit": o.potential_benefit,
        "assumptions": o.assumptions,
        "nextAction": o.recommended_action,
        "potentialValue": num(o.scenario_value),
        "realizedValue": num(o.realized_value),
        "generatedBy": o.generated_by,
        "syntheticDemo": o.synthetic_demo,
    }


def finding(f: WorkspaceFinding, agent_ref: str | None, run_ref: str | None) -> dict:
    return {
        "id": f.ref,
        "companyId": sid(f.company_id),
        "title": f.title,
        "detail": f.detail,
        "severity": f.severity,
        "status": f.status,
        "agentId": agent_ref,
        "runId": run_ref,
        "foundAt": iso(f.found_at),
    }


def agent(a: WorkspaceAgent) -> dict:
    p = a.payload or {}
    return {
        "id": a.ref,
        "companyId": sid(a.company_id),
        "name": a.name,
        "represents": p.get("represents", ""),
        "status": a.status,
        "lastRunAt": iso(a.last_run_at),
        "cases": p.get("cases", 0),
        "review": p.get("review", 0),
        "findings": p.get("findings", 0),
        "lastFailure": p.get("lastFailure"),
        "cost": p.get("cost", 0),
    }


def run(r: WorkspaceAgentRun, agent_ref: str) -> dict:
    p = r.payload or {}
    return {
        "id": r.ref,
        "agentId": agent_ref,
        "companyId": sid(r.company_id),
        "goal": p.get("goal", ""),
        "startedAt": iso(r.started_at),
        "status": r.status,
        "sources": p.get("sources", []),
        "events": p.get("events", []),
        "output": p.get("output", ""),
        "evidence": p.get("evidence", []),
        "corrections": p.get("corrections", []),
        "modelCost": num(r.model_cost),
        "needsReview": r.needs_review,
    }


def activity(a: PortfolioActivity) -> dict:
    return {"at": iso(a.at), "companyId": sid(a.company_id), "text": a.text, "kind": a.kind}


def company_profile(fc: FirmCompany) -> dict:
    return {
        "id": sid(fc.id),
        "slug": fc.slug,
        "name": fc.name,
        "location": fc.location,
        "industry": fc.industry,
        "description": fc.description,
        "acquired": iso(fc.acquisition_date),
        "status": fc.status,
        "syntheticDemo": fc.synthetic_demo,
        "analysisRunAt": iso(fc.analysis_run_at),
    }


def source_file(f: SourceFile) -> dict:
    return {
        "id": sid(f.id),
        "filename": f.filename,
        "mimeType": f.mime_type,
        "size": f.file_size,
        "uploadedAt": iso(f.uploaded_at),
        "status": f.status,
    }


def field_mapping(m: FieldMapping) -> dict:
    return {
        "id": sid(m.id),
        "source": m.source_column,
        "example": m.example_value,
        "target": m.target_field,
        "targetEntity": m.target_entity,
        "confidence": m.confidence,
        "reason": m.reason,
        "status": {"proposed": "Ready", "needs_review": "Review", "approved": "Confirmed", "ignored": "Ready"}[m.status],
    }


def import_exception(x: ImportException) -> dict:
    d = x.detail or {}
    return {
        "id": x.ref,
        "uuid": sid(x.id),
        "importJobId": sid(x.import_job_id),
        "type": x.exception_type,
        "description": x.description,
        "left": d.get("left"),
        "right": d.get("right"),
        "leftRow": d.get("leftRow"),
        "rightRow": d.get("rightRow"),
        "dataset": d.get("dataset"),
        "matchVendor": d.get("matchVendor"),
        "actions": d.get("actions", []),
        "candidates": x.candidate_matches,
        "confidence": x.confidence,
        "decision": x.resolution,
        "open": x.status == "open",
    }


def import_job(j: ImportJob, file: SourceFile | None = None, mappings: list[FieldMapping] | None = None) -> dict:
    out = {
        "id": sid(j.id),
        "companyId": sid(j.company_id),
        "sourceFileId": sid(j.source_file_id),
        "filename": file.filename if file else None,
        "status": j.status,
        "dataset": j.dataset_type,
        "detection": {"dataset": j.dataset_type, "confidence": j.detection_confidence},
        "sheet": j.sheet_name,
        "columns": j.columns,
        "processor": j.processor,
        "recordsDetected": j.records_detected,
        "recordsImported": j.records_imported,
        "recordsNeedingReview": j.records_needing_review,
        "recordsRejected": j.records_rejected,
        "error": j.error,
        "createdAt": iso(j.created_at),
        "startedAt": iso(j.started_at),
        "completedAt": iso(j.completed_at),
    }
    if mappings is not None:
        out["mappings"] = [field_mapping(m) for m in mappings]
    return out
