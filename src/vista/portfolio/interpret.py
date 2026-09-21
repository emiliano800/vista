"""Interpretation layer over canonical rows (the fact layer's output).

Two run types, both durable jobs under the A2A contract in AGENTS.md:

* `canonical_review` (File Reviewer) — one company tenant. Reads only canonical
  tables (customers, invoices, vendors, purchases, subscriptions, policies,
  purchase orders/lines, inventory), profiles them, applies code-computed checks
  and one model call per table, and writes `findings` (ledger), their workspace
  mirrors, a `company_summaries` narrative and a follow-up task per High finding.
* `portfolio_merge` (Sector Merger) — firm home tenant. Re-validates every company
  id in the payload against `platform.firm_companies`, joins one sector's canonical
  tables on exact shared keys, confirms candidates with one model call per
  opportunity kind, and writes `platform.opportunities` + ledger findings.

Nothing here touches source files or canonical rows: reads go through the
canonical tables, writes go to findings / summaries / tasks / opportunities /
agent ledger only. Reruns are idempotent (dedupe on kind + title + company for
findings, category + shared key + company set for opportunities).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from vista.agents import analyze, discover
from vista.agents.keys import agent_key_for
from vista.agents.llm import chat
from vista.agents.runtime import pricing, run_phase
from vista.agents.synthetic import Company, Table
from vista.auth import Principal
from vista.db import platform_session, tenant_session
from vista.jobs.queue import enqueue
from vista.models.platform import FirmCompany, Job, Opportunity, Tenant, User
from vista.models.tenant import (
    AgentRun,
    AgentRunEvent,
    CompanySummary,
    Customer,
    FieldMapping,
    Finding,
    ImportJob,
    InventoryBalance,
    Invoice,
    Policy,
    PurchaseOrder,
    PurchaseOrderLine,
    Subscription,
    Task,
    UsageEvent,
    Vendor,
    VendorPurchase,
    WorkspaceAgent,
    WorkspaceAgentRun,
    WorkspaceFinding,
)
from vista.portfolio.access import CompanyRef, FirmContext, company_session, load_firm_context, reporting_period, today
from vista.portfolio.service import log_activity, money2, next_ref

RUN_REVIEW = "canonical_review"
RUN_MERGE = "portfolio_merge"
INSURANCE_WORDS = ("insurance", "broker", "brokerage", "agency", "p&c", "benefits")
OPPORTUNITY_CATEGORY = {
    "purchasing_price_gap": "Purchasing",
    "vendor_consolidation": "Vendor consolidation",
    "software_overlap": "Software",
    "freight_rate_gap": "Freight",
    "carrier_consolidation": "Carrier consolidation",
    "cross_sell": "Cross-sell",
}
SCREEN_CONFIDENCE = 0.6  # exact-key join found in code but not yet confirmed by the model
MAX_EVIDENCE_IDS = 25


def sector_of(industry: str) -> str:
    return "insurance_broking" if any(w in industry.lower() for w in INSURANCE_WORDS) else "industrial_goods"


def company_for(ref: CompanyRef) -> Company:
    """The analyze/discover phases identify companies by short name; the workspace uses slug + name."""
    return Company(slug=ref.slug, name=ref.name, short=ref.name.split()[0], sector=sector_of(ref.row.industry), tier="imported")


# ---- Canonical rows -> tables the existing phases understand ------------------------------------


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, Decimal):
        return f"{v.normalize():f}" if v == v.to_integral() else str(v)
    return str(v)


def _table(name: str, rows: list[dict]) -> Table | None:
    if not rows:
        return None
    return Table(ref=f"canonical/{name}", columns=list(rows[0].keys()), rows=rows)


def canonical_tables(ts: Session) -> list[Table]:
    """Snake_case projections of the canonical tables. Every row carries its `id`
    so downstream evidence can cite the record, and table names match the ones
    `analyze.KIND_TABLES` already knows (`customers`, `vendors`, `policies`, ...)."""
    vendor_name = {v.id: v.source_name for v in ts.scalars(select(Vendor))}
    tables = [
        _table(
            "customers",
            [
                {
                    "id": _s(c.id),
                    "source_customer_id": c.source_customer_id,
                    "customer_name": c.display_name,
                    "contact": c.contact_name,
                    "email": c.email,
                    "city": c.city,
                    "state": c.state,
                    "service_type": c.service_type,
                    "status": c.status,
                }
                for c in ts.scalars(select(Customer).order_by(Customer.display_name))
            ],
        ),
        _table(
            "invoices",
            [
                {
                    "id": _s(i.id),
                    "invoice_number": i.source_invoice_number,
                    "customer_name": i.customer_name,
                    "issue_date": _s(i.issue_date),
                    "due_date": _s(i.due_date),
                    "amount": _s(i.amount),
                    "outstanding_balance": _s(i.outstanding_balance),
                    "status": i.status,
                }
                for i in ts.scalars(select(Invoice).order_by(Invoice.issue_date))
            ],
        ),
        _table(
            "vendors",
            [
                {
                    "id": _s(v.id),
                    "source_vendor_id": v.source_vendor_id,
                    "vendor_name": v.source_name,
                    "category": v.category,
                    "payment_terms": v.payment_terms,
                    "status": v.status,
                }
                for v in ts.scalars(select(Vendor).order_by(Vendor.normalized_name))
            ],
        ),
        _table(
            "ap_vendor_invoices",
            [
                {
                    "id": _s(p.id),
                    "vendor_name": vendor_name.get(p.vendor_id, ""),
                    "date": _s(p.purchase_date),
                    "sku": p.sku,
                    "description": p.item_description,
                    "quantity": _s(p.quantity),
                    "unit_price": _s(p.unit_price),
                    "amount": _s(p.total_amount),
                }
                for p in ts.scalars(select(VendorPurchase).order_by(VendorPurchase.purchase_date))
            ],
        ),
        _table(
            "software_subscriptions",
            [
                {
                    "id": _s(s.id),
                    "vendor": s.vendor_name,
                    "product": s.product_name,
                    "function": s.category,
                    "monthly_cost": _s(s.monthly_cost),
                    "annual_cost": _s(s.annual_cost),
                    "seats": _s(s.seat_count),
                    "renewal_date": _s(s.renewal_date),
                }
                for s in ts.scalars(select(Subscription).order_by(Subscription.product_name))
            ],
        ),
        _table(
            "policies",
            [
                {
                    "id": _s(p.id),
                    "policy_number": p.policy_number,
                    "customer_name": p.customer_name,
                    "line_of_business": p.line_of_business,
                    "carrier_code": p.carrier_code,
                    "carrier_name": p.carrier_name,
                    "effective_date": _s(p.effective_date),
                    "expiration_date": _s(p.expiration_date),
                    "annual_premium": _s(p.annual_premium),
                    "commission_pct": _s(p.commission_pct),
                    "expected_commission": _s(p.expected_commission),
                    "status": p.status,
                }
                for p in ts.scalars(select(Policy).order_by(Policy.expiration_date))
            ],
        ),
        _table(
            "purchase_orders",
            [
                {
                    "id": _s(o.id),
                    "po_number": o.po_number,
                    "supplier_name": o.supplier_name,
                    "po_date": _s(o.po_date),
                    "payment_terms": o.payment_terms,
                    "total_amount": _s(o.total_amount),
                    "status": o.status,
                }
                for o in ts.scalars(select(PurchaseOrder).order_by(PurchaseOrder.po_date))
            ],
        ),
        _table(
            "items",  # PO lines carry manufacturer part numbers + unit cost: the price-gap screen's input
            [
                {
                    "id": _s(line.id),
                    "po_number": line.po_number,
                    "item_id": line.item_id,
                    "description": line.description,
                    "manufacturer_part_number": line.manufacturer_part_number,
                    "ordered_qty": _s(line.ordered_qty),
                    "received_qty": _s(line.received_qty),
                    "unit_cost": _s(line.unit_cost),
                    "promised_date": _s(line.promised_date),
                    "status": line.status,
                }
                for line in ts.scalars(select(PurchaseOrderLine).order_by(PurchaseOrderLine.po_number, PurchaseOrderLine.line_number))
            ],
        ),
        _table(
            "inventory_balances",
            [
                {
                    "id": _s(b.id),
                    "item_id": b.item_id,
                    "warehouse": b.warehouse,
                    "on_hand_qty": _s(b.on_hand_qty),
                    "allocated_qty": _s(b.allocated_qty),
                    "available_qty": _s(b.available_qty),
                    "unit_cost": _s(b.unit_cost),
                    "extended_value": _s(b.extended_value),
                    "last_count_date": _s(b.last_count_date),
                }
                for b in ts.scalars(select(InventoryBalance).order_by(InventoryBalance.item_id))
            ],
        ),
    ]
    return [t for t in tables if t is not None]


# ---- Ledger helpers (same shape as jobs.handlers) ----------------------------------------------


def _next_seq(ts: Session, run_id: uuid.UUID) -> int:
    return 1 + (ts.scalar(select(AgentRunEvent.seq).where(AgentRunEvent.run_id == run_id).order_by(AgentRunEvent.seq.desc()).limit(1)) or 0)


def _emit(ts: Session, run_id: uuid.UUID, seq: int, event_type: str, data: dict) -> int:
    ts.add(AgentRunEvent(run_id=run_id, seq=seq, event_type=event_type, data=data))
    return seq + 1


def _usage(ts: Session, run: AgentRun, result) -> Decimal:
    in_price, out_price = pricing(result.model)
    cost = Decimal(result.input_tokens) * in_price + Decimal(result.output_tokens) * out_price
    ts.add(
        UsageEvent(
            run_id=run.id,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=cost,
            agent_key=run.agent_key,
            company=run.company,
        )
    )
    return cost


def _start(ts: Session, run_id: uuid.UUID) -> AgentRun:
    run = ts.get(AgentRun, run_id)
    if run is None:
        raise RuntimeError(f"agent run {run_id} not found")
    run.status, run.started_at, run.error = "running", run.started_at or datetime.now(UTC), None
    run.agent_key = run.agent_key or agent_key_for(run.run_type)
    return run


def _finish(ts: Session, run: AgentRun, seq: int, result: dict) -> None:
    _emit(ts, run.id, seq, "result", result)
    run.status, run.finished_at = "succeeded", datetime.now(UTC)


@dataclass(frozen=True)
class Structured:
    """Machine-readable layer of a finding: what it is about and what it does to analysis built on those records."""

    finding_type: str  # dotted: data_quality.duplicate_entity, timing.renewal_window, ...
    severity: str  # High|Medium|Low
    effect: str  # block|degrade|enrich
    affected_entities: list[dict]  # [{"type": "vendor", "id": "<uuid>"}]

    @property
    def blocking(self) -> bool:
        return self.effect == EFFECT_BLOCK


EFFECT_BLOCK, EFFECT_DEGRADE, EFFECT_ENRICH = "block", "degrade", "enrich"
EFFECT_RANK = {EFFECT_ENRICH: 0, EFFECT_DEGRADE: 1, EFFECT_BLOCK: 2}
MAX_AFFECTED_ENTITIES = 2000


def _upsert_finding(
    ts: Session,
    run: AgentRun,
    kind: str,
    title: str,
    detail: str,
    evidence: dict,
    structured: Structured | None = None,
) -> tuple[Finding, bool]:
    """Ledger finding keyed by (agent, kind, title, company). Dismissed rows stay dismissed."""
    title = title[:512]
    existing = ts.scalar(
        select(Finding).where(
            Finding.agent_key == run.agent_key, Finding.kind == kind, Finding.title == title, Finding.company == run.company
        )
    )
    f = existing or Finding(run_id=run.id, agent_key=run.agent_key, company=run.company, kind=kind, title=title)
    f.detail, f.evidence, f.run_id = detail, evidence, run.id
    if structured is not None:
        f.finding_type, f.severity, f.effect, f.blocking = (
            structured.finding_type,
            structured.severity,
            structured.effect,
            structured.blocking,
        )
        f.affected_entities = structured.affected_entities[:MAX_AFFECTED_ENTITIES]
    if existing is not None:
        return existing, False
    ts.add(f)
    ts.flush()
    return f, True


# ---- File Reviewer over canonical rows ---------------------------------------------------------


@dataclass
class Check:
    title: str
    detail: str
    severity: str  # High|Medium|Low
    kind: str  # observed_fact|inefficiency
    table: str
    ids: list[str]  # every canonical record the check is about (evidence is capped, affected_entities is not)
    metric: dict
    finding_type: str
    effect: str

    def structured(self) -> Structured:
        entity = ENTITY_OF_TABLE[self.table]
        return Structured(self.finding_type, self.severity, self.effect, [{"type": entity, "id": i} for i in self.ids])


# canonical table name -> entity type used in affected_entities and in the merger's evidence refs
ENTITY_OF_TABLE = {
    "customers": "customer",
    "invoices": "invoice",
    "vendors": "vendor",
    "ap_vendor_invoices": "vendor_purchase",
    "software_subscriptions": "subscription",
    "policies": "policy",
    "purchase_orders": "purchase_order",
    "purchase_order_lines": "purchase_order_line",
    "inventory_balances": "inventory_balance",
}
# field_mappings.target_entity -> (canonical model, table) so mapping_suspect can name the rows an uncertain mapping produced
MODEL_OF_ENTITY = {
    "customer": (Customer, "customers"),
    "invoice": (Invoice, "invoices"),
    "vendor": (Vendor, "vendors"),
    "vendor_purchase": (VendorPurchase, "ap_vendor_invoices"),
    "subscription": (Subscription, "software_subscriptions"),
    "policy": (Policy, "policies"),
    "purchase_order": (PurchaseOrder, "purchase_orders"),
    "purchase_order_line": (PurchaseOrderLine, "purchase_order_lines"),
    "inventory_balance": (InventoryBalance, "inventory_balances"),
}
MAPPED_MONEY_FIELDS = {
    "unit_cost",
    "unit_price",
    "amount",
    "outstanding_balance",
    "annual_premium",
    "expected_commission",
    "commission_pct",
    "monthly_cost",
    "annual_cost",
    "total_amount",
    "ordered_qty",
    "received_qty",
    "on_hand_qty",
    "extended_value",
}
MAPPING_SUSPECT_CONFIDENCE = 0.8


def _ids(rows) -> list[str]:
    return [str(r.id) for r in rows]


def mapping_suspect_checks(ts: Session) -> list[Check]:
    """A money/quantity column whose header mapping scored below the confidence bar, or was proposed by the
    model and never confirmed (confirmation sets confidence to 1.0), makes every row it produced unreliable
    for price and spend comparisons."""
    suspect = ts.scalars(
        select(FieldMapping)
        .join(ImportJob, ImportJob.id == FieldMapping.import_job_id)
        .where(
            ImportJob.status == "completed",
            FieldMapping.status == "approved",
            FieldMapping.target_field.in_(sorted(MAPPED_MONEY_FIELDS)),
            (FieldMapping.confidence < MAPPING_SUSPECT_CONFIDENCE)
            | (FieldMapping.reason.like("model:%") & (FieldMapping.confidence < 1.0)),
        )
    ).all()
    by_entity: dict[str, list[FieldMapping]] = defaultdict(list)
    for m in suspect:
        if m.target_entity in MODEL_OF_ENTITY:
            by_entity[m.target_entity].append(m)
    out: list[Check] = []
    for entity, ms in sorted(by_entity.items()):
        model, table = MODEL_OF_ENTITY[entity]
        job_ids = sorted({m.import_job_id for m in ms})
        rows = ts.scalars(select(model).where(model.import_job_id.in_(job_ids))).all()
        if not rows:
            continue
        cols = "; ".join(f"'{m.source_column}' → {m.target_field} ({m.confidence:.2f})" for m in ms[:4])
        out.append(
            Check(
                f"{len(rows)} {table.replace('_', ' ')} rows rest on an unconfirmed column mapping",
                f"Money/quantity columns mapped without analyst confirmation: {cols}. "
                "Values in these rows should not drive price or spend comparisons until the mapping is confirmed.",
                "High",
                "observed_fact",
                table,
                _ids(rows),
                {"count": len(rows), "mappings": len(ms), "import_jobs": [str(j) for j in job_ids]},
                "data_quality.mapping_suspect",
                EFFECT_BLOCK,
            )
        )
    return out


def canonical_checks(ts: Session, now: date) -> list[Check]:
    """Code-computed observations; each cites the canonical record ids behind it."""
    out: list[Check] = mapping_suspect_checks(ts)
    period = reporting_period(now)

    late = [
        i
        for i in ts.scalars(select(Invoice).where(Invoice.outstanding_balance > 0, Invoice.due_date.is_not(None)))
        if (now - i.due_date).days > 90
    ]
    if late:
        total = sum((i.outstanding_balance for i in late), Decimal(0))
        by_customer: dict[str, Decimal] = defaultdict(Decimal)
        for i in late:
            by_customer[i.customer_name] += i.outstanding_balance
        top = sorted(by_customer.items(), key=lambda kv: -kv[1])[:3]
        out.append(
            Check(
                f"{len(late)} invoices are more than 90 days past due ({money2(total)} outstanding)",
                "Largest balances: " + "; ".join(f"{n} {money2(v)}" for n, v in top) + ".",
                "High" if total >= 25_000 or len(late) >= 10 else "Medium",
                "observed_fact",
                "invoices",
                _ids(sorted(late, key=lambda i: -i.outstanding_balance)),
                {"count": len(late), "outstanding": float(total)},
                "working_capital.overdue_ar",
                EFFECT_ENRICH,
            )
        )
    inverted = [
        i
        for i in ts.scalars(select(Invoice).where(Invoice.issue_date.is_not(None), Invoice.due_date.is_not(None)))
        if i.due_date < i.issue_date
    ]
    if inverted:
        out.append(
            Check(
                f"{len(inverted)} invoices are due before they were issued",
                "Due date precedes issue date; the source's date columns or terms are probably wrong for these rows.",
                "Medium",
                "observed_fact",
                "invoices",
                _ids(inverted),
                {"count": len(inverted)},
                "data_quality.date_inversion",
                EFFECT_DEGRADE,
            )
        )

    vendors = ts.scalars(select(Vendor)).all()
    by_key: dict[str, list[Vendor]] = defaultdict(list)
    for v in vendors:
        by_key[analyze.vendor_key(v.source_name)].append(v)
    dupes = [vs for vs in by_key.values() if len({v.source_name for v in vs}) > 1]
    if dupes:
        sample = "; ".join(" / ".join(sorted({v.source_name for v in vs})) for vs in dupes[:4])
        out.append(
            Check(
                f"{len(dupes)} vendor names look like the same supplier spelled differently",
                f"Vendor master has near-duplicate names: {sample}. Spend on these is split across records.",
                "Medium",
                "inefficiency",
                "vendors",
                _ids([v for vs in dupes for v in vs]),
                {"groups": len(dupes)},
                "data_quality.duplicate_entity",
                EFFECT_DEGRADE,
            )
        )

    subs = ts.scalars(select(Subscription)).all()
    by_fn: dict[str, list[Subscription]] = defaultdict(list)
    for s in subs:
        if s.category:
            by_fn[s.category.lower()].append(s)
    overlap = {fn: ss for fn, ss in by_fn.items() if len({s.product_name for s in ss}) > 1}
    if overlap:
        cost = sum((s.annual_cost for ss in overlap.values() for s in ss), Decimal(0))
        sample = "; ".join(f"{fn}: {', '.join(sorted({s.product_name for s in ss}))}" for fn, ss in list(overlap.items())[:4])
        out.append(
            Check(
                f"{len(overlap)} software functions are covered by more than one product ({money2(cost)}/yr)",
                sample + ".",
                "Medium",
                "inefficiency",
                "software_subscriptions",
                _ids([s for ss in overlap.values() for s in ss]),
                {"functions": len(overlap), "annual_cost": float(cost)},
                "spend.software_overlap",
                EFFECT_ENRICH,
            )
        )
    renewing = [s for s in subs if s.renewal_date and 0 <= (s.renewal_date - now).days <= 60]
    if renewing:
        out.append(
            Check(
                f"{len(renewing)} software subscriptions renew within 60 days",
                ", ".join(f"{s.product_name} ({s.renewal_date.isoformat()})" for s in renewing[:6]) + ".",
                "Low",
                "observed_fact",
                "software_subscriptions",
                _ids(renewing),
                {"count": len(renewing)},
                "timing.renewal_window",
                EFFECT_ENRICH,
            )
        )

    policies = ts.scalars(select(Policy)).all()
    expiring = [
        p
        for p in policies
        if p.expiration_date and 0 <= (p.expiration_date - now).days <= 90 and p.status not in ("cancelled", "non_renewed")
    ]
    if expiring:
        premium = sum((p.annual_premium for p in expiring), Decimal(0))
        out.append(
            Check(
                f"{len(expiring)} policies expire within 90 days ({money2(premium)} premium up for renewal)",
                "Renewal pipeline from the policy register; expected commission at stake "
                f"{money2(sum((p.expected_commission for p in expiring), Decimal(0)))}.",
                "Medium",
                "observed_fact",
                "policies",
                _ids(sorted(expiring, key=lambda p: p.expiration_date)),
                {"count": len(expiring), "premium": float(premium)},
                "timing.policy_expiry",
                EFFECT_ENRICH,
            )
        )
    off = [
        p
        for p in policies
        if p.annual_premium
        and p.commission_pct
        and abs(p.expected_commission - p.annual_premium * p.commission_pct / 100)
        > max(Decimal("1"), p.expected_commission * Decimal("0.01"))
    ]
    if off:
        out.append(
            Check(
                f"{len(off)} policies' expected commission does not equal premium × commission rate",
                "Recorded expected commission differs from premium × rate by more than 1%; carrier statement reconciliation will not tie.",
                "High" if len(off) >= 10 else "Medium",
                "observed_fact",
                "policies",
                _ids(off),
                {"count": len(off)},
                "data_quality.reconciliation_failure",
                EFFECT_DEGRADE,
            )
        )
    swapped = [p for p in policies if p.effective_date and p.expiration_date and p.expiration_date < p.effective_date]
    if swapped:
        out.append(
            Check(
                f"{len(swapped)} policies expire before they take effect",
                "Effective and expiration dates are inverted on these rows.",
                "Medium",
                "observed_fact",
                "policies",
                _ids(swapped),
                {"count": len(swapped)},
                "data_quality.date_inversion",
                EFFECT_DEGRADE,
            )
        )

    late_lines = [
        line
        for line in ts.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.promised_date.is_not(None)))
        if line.promised_date < now and line.received_qty < line.ordered_qty and line.status not in ("closed", "cancelled")
    ]
    if late_lines:
        value = sum(((line.ordered_qty - line.received_qty) * line.unit_cost for line in late_lines), Decimal(0))
        out.append(
            Check(
                f"{len(late_lines)} purchase-order lines are past their promised date and not fully received",
                f"Open quantity at unit cost is {money2(value)}; suppliers on these lines are candidates for expediting.",
                "High" if len(late_lines) >= 25 else "Medium",
                "inefficiency",
                "purchase_order_lines",
                _ids(sorted(late_lines, key=lambda line: line.promised_date)),
                {"count": len(late_lines), "open_value": float(value)},
                "operations.late_receipt",
                EFFECT_ENRICH,
            )
        )

    balances = ts.scalars(select(InventoryBalance)).all()
    unrec = [b for b in balances if abs(b.on_hand_qty - b.allocated_qty - b.available_qty) > Decimal("0.001")]
    if unrec:
        out.append(
            Check(
                f"{len(unrec)} inventory balances do not reconcile (on hand − allocated ≠ available)",
                "Available quantity disagrees with on-hand minus allocated; cycle-count or allocation logic issue.",
                "Medium",
                "observed_fact",
                "inventory_balances",
                _ids(unrec),
                {"count": len(unrec)},
                "data_quality.reconciliation_failure",
                EFFECT_DEGRADE,
            )
        )
    negative = [b for b in balances if b.on_hand_qty < 0]
    if negative:
        out.append(
            Check(
                f"{len(negative)} inventory balances are negative",
                "Negative on-hand quantities indicate issues were posted before receipts or counts are stale.",
                "Medium",
                "observed_fact",
                "inventory_balances",
                _ids(negative),
                {"count": len(negative)},
                "data_quality.negative_balance",
                EFFECT_DEGRADE,
            )
        )
    stale = [b for b in balances if b.last_count_date and (now - b.last_count_date).days > 365 and b.extended_value > 0]
    if stale:
        stale_value = sum((b.extended_value for b in stale), Decimal(0))
        out.append(
            Check(
                f"{len(stale)} stocked items have not been counted in over a year ({money2(stale_value)})",
                "Book value rests on counts older than twelve months.",
                "Low",
                "observed_fact",
                "inventory_balances",
                _ids(stale),
                {"count": len(stale)},
                "data_quality.stale_count",
                EFFECT_ENRICH,
            )
        )

    active = {
        i.customer_id for i in ts.scalars(select(Invoice).where(Invoice.issue_date.between(period.start, period.end))) if i.customer_id
    }
    customers = ts.scalars(select(Customer).where(Customer.status == "active")).all()
    dormant = [c for c in customers if c.id not in active] if active else []
    if dormant and len(dormant) >= max(3, len(customers) // 10):
        out.append(
            Check(
                f"{len(dormant)} active customers were not invoiced in the reporting period",
                f"{len(dormant)} of {len(customers)} active customer records have no invoice between {period.start} and {period.end}.",
                "Low",
                "observed_fact",
                "customers",
                _ids(dormant),
                {"count": len(dormant)},
                "revenue.dormant_customers",
                EFFECT_ENRICH,
            )
        )
    return out


def _workspace_agent(ts: Session, company: CompanyRef, ref: str, name: str, represents: str) -> WorkspaceAgent:
    a = ts.scalar(select(WorkspaceAgent).where(WorkspaceAgent.ref == ref))
    if a is None:
        a = WorkspaceAgent(
            company_id=company.id,
            ref=ref,
            name=name,
            status="Active",
            payload={"represents": represents, "cases": 0, "review": 0, "findings": 0, "cost": 0},
        )
        ts.add(a)
        ts.flush()
    return a


def _mirror_finding(
    ts: Session,
    platform: Session,
    ctx: FirmContext,
    company: CompanyRef,
    agent: WorkspaceAgent,
    run: WorkspaceAgentRun,
    title: str,
    detail: str,
    severity: str,
    at: datetime,
) -> WorkspaceFinding:
    wf = ts.scalar(select(WorkspaceFinding).where(WorkspaceFinding.company_id == company.id, WorkspaceFinding.title == title))
    if wf is None:
        wf = WorkspaceFinding(
            company_id=company.id,
            ref=next_ref(platform, ctx.firm.id, "finding", "F", 3),
            agent_id=agent.id,
            run_id=run.id,
            title=title,
            detail=detail,
            severity=severity,
            status="Open",
            found_at=at,
        )
        ts.add(wf)
        ts.flush()
    else:
        wf.detail, wf.severity, wf.run_id = detail, severity, run.id
    return wf


def _task_for(
    ts: Session, platform: Session, ctx: FirmContext, company: CompanyRef, wf: WorkspaceFinding, check: Check, at: datetime
) -> Task | None:
    if ts.scalar(select(Task).where(Task.source_type == "finding", Task.source_id == wf.ref)) is not None:
        return None
    t = Task(
        company_id=company.id,
        ref=next_ref(platform, ctx.firm.id, "task", "T", 3),
        title=f"Review: {check.title}"[:255],
        description=f"{check.detail} Evidence: {len(check.ids)} canonical {check.table} records cited on finding {wf.ref}."[:4000],
        category="Agent exception",
        source_type="finding",
        source_id=wf.ref,
        priority=check.severity,
        status="Open",
        created_by="File Reviewer",
        created_at=at,
    )
    ts.add(t)
    return t


def review_company(
    platform: Session,
    ctx: FirmContext,
    company: CompanyRef,
    run_id: uuid.UUID,
    job_id: uuid.UUID,
    parent_run_id: str | None = None,
) -> dict:
    """File Reviewer body: canonical tables in, findings / summary / tasks out."""
    now = today()
    at = datetime.now(UTC)
    with company_session(company) as ts:
        run = _start(ts, run_id)
        run.company = company_for(company).short
        seq = _emit(
            ts,
            run_id,
            _next_seq(ts, run_id),
            "step",
            {"message": "started", "parent_run_id": parent_run_id, "company_id": str(company.id), "job_id": str(job_id)},
        )
        tables = canonical_tables(ts)
        seq = _emit(ts, run_id, seq, "tool_call", {"tool": "read_canonical", "tables": {t.name: len(t.rows) for t in tables}})
        cost = Decimal(0)
        created = updated = 0
        model_facts = 0
        events: list[list[str]] = [
            [at.strftime("%H:%M"), f"Loaded {sum(len(t.rows) for t in tables):,} canonical rows across {len(tables)} tables."]
        ]
        agent = _workspace_agent(
            ts,
            company,
            f"file-reviewer-{company.slug}",
            "File Reviewer",
            "Imported canonical records — data quality and working-capital review",
        )
        n_runs = len(ts.scalars(select(WorkspaceAgentRun.id).where(WorkspaceAgentRun.agent_id == agent.id)).all())
        wrun = WorkspaceAgentRun(
            company_id=company.id,
            agent_id=agent.id,
            ref=f"run-{agent.ref}-{n_runs + 1:04d}",
            status="Complete",
            started_at=at,
            payload={
                "goal": "Review the canonical records the fact layer imported and surface evidence-linked observations.",
                "sources": [f"canonical/{t.name}" for t in tables],
                "events": events,
                "output": "",
                "evidence": [],
                "corrections": [],
                "ledger_run_id": str(run_id),
            },
        )
        ts.add(wrun)
        ts.flush()

        checks = canonical_checks(ts, now)
        high = 0
        for c in checks:
            evidence = {
                "table": f"canonical/{c.table}",
                "record_ids": c.ids[:MAX_EVIDENCE_IDS],
                "record_count": len(c.ids),
                "metric": c.metric,
                "confidence": 1.0,
                "computed_by": "code",
            }
            f, is_new = _upsert_finding(ts, run, c.kind, c.title, c.detail, evidence, c.structured())
            created += is_new
            updated += not is_new
            seq = _emit(
                ts,
                run_id,
                seq,
                "finding",
                {
                    "finding_id": str(f.id),
                    "title": f.title,
                    "severity": c.severity,
                    "finding_type": c.finding_type,
                    "effect": c.effect,
                    "affected": len(c.ids),
                },
            )
            wf = _mirror_finding(ts, platform, ctx, company, agent, wrun, c.title, c.detail, c.severity, at)
            events.append([at.strftime("%H:%M"), f"{c.severity}: {c.title}"])
            if c.severity == "High":
                high += 1
                if wf.status == "Open":
                    _task_for(ts, platform, ctx, company, wf, c, at)

        for table in tables:
            profile = discover.profile_table(table)
            phase = run_phase(
                discover.prepare(company_for(company), profile), discover.parse, lambda out, p=profile: discover.apply(out, p), llm=chat
            )
            r = phase.result
            seq = _emit(
                ts,
                run_id,
                seq,
                "model_call",
                {
                    "table": table.ref,
                    "model": r.model,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "source": r.source,
                },
            )
            cost += _usage(ts, run, r)
            for fact in phase.rows:
                if fact["confidence"] < 0.6:
                    continue
                title = f"{table.name}: {fact['subject']} {fact['predicate']}"
                f, is_new = _upsert_finding(
                    ts,
                    run,
                    "observed_fact",
                    title,
                    fact["value"],
                    {**fact["source_ref"], "confidence": fact["confidence"], "computed_by": "model" if r.source != "stub" else "profile"},
                )
                created += is_new
                updated += not is_new
                model_facts += 1
                seq = _emit(ts, run_id, seq, "finding", {"finding_id": str(f.id), "title": f.title})
                if fact["confidence"] >= 0.85:
                    _mirror_finding(ts, platform, ctx, company, agent, wrun, title, fact["value"], "Low", at)

        stats = {"tables": {t.name: len(t.rows) for t in tables}, "checks": len(checks), "high": high, "model_facts": model_facts}
        narrative = _narrative(company, checks, stats)
        ts.add(CompanySummary(run_id=run_id, content=narrative, stats=stats))
        wrun.payload = {
            **wrun.payload,
            "events": events,
            "output": narrative,
            "evidence": [{"entity": c.table, "ids": c.ids[:5], "companyId": str(company.id)} for c in checks],
        }
        wrun.model_cost = cost.quantize(Decimal("0.0001"))
        wrun.needs_review = high
        agent.last_run_at = at
        agent.payload = {
            **(agent.payload or {}),
            "cases": (agent.payload or {}).get("cases", 0) + len(checks),
            "review": high,
            "findings": len(checks) + model_facts,
            "cost": round(float((agent.payload or {}).get("cost", 0)) + float(cost), 4),
        }
        _finish(ts, run, seq, {"findings_created": created, "findings_updated": updated, "high": high, "cost_usd": str(cost)})
        ts.commit()
    log_activity(
        platform,
        ctx.firm.id,
        company.id,
        f"File Reviewer reviewed {company.name}'s canonical records: {len(checks)} observations, {high} high severity.",
        "agent",
        at,
    )
    return {"checks": len(checks), "high": high, "created": created, "updated": updated, "model_facts": model_facts, "cost_usd": str(cost)}


def _narrative(company: CompanyRef, checks: list[Check], stats: dict) -> str:
    rows = ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in stats["tables"].items())
    parts = [f"{company.name}: reviewed {rows}."]
    if checks:
        parts.append("Observations: " + " ".join(f"({c.severity}) {c.title}." for c in checks))
    else:
        parts.append("No code-computed anomalies in the canonical records.")
    return " ".join(parts)


# ---- Sector Merger over canonical rows ----------------------------------------------------------


def _authorized(platform: Session, firm_id: uuid.UUID, company_ids: list[str]) -> list[CompanyRef]:
    rows = platform.execute(
        select(FirmCompany, Tenant.schema_name)
        .join(Tenant, Tenant.id == FirmCompany.tenant_id)
        .where(FirmCompany.firm_id == firm_id, FirmCompany.id.in_([uuid.UUID(c) for c in company_ids]))
    ).all()
    return [CompanyRef(fc.id, fc.slug, fc.name, fc.tenant_id, schema, fc) for fc, schema in rows]


def _upsert_opportunity(
    platform: Session,
    firm_id: uuid.UUID,
    row: dict,
    refs_by_short: dict[str, CompanyRef],
    evidence: list[dict],
    generated_by: str,
    lineage: dict | None = None,
) -> tuple[Opportunity, bool]:
    ids = [str(refs_by_short[s].id) for s in row["companies"]]
    category = OPPORTUNITY_CATEGORY[row["kind"]]
    key = row["shared_key"].strip()
    existing = platform.scalars(select(Opportunity).where(Opportunity.firm_id == firm_id, Opportunity.category == category)).all()
    match = next((o for o in existing if (o.sku or "").lower() == key.lower() and set(o.company_ids) == set(ids)), None)
    value = Decimal(str(row["estimated_annual_value"])) if row.get("estimated_annual_value") is not None else None
    if match is not None:
        match.title, match.observed_fact, match.evidence, match.generated_by = row["title"], row["detail"], evidence, generated_by
        match.lineage = lineage or {}
        match.assumptions = row.get("assumptions", match.assumptions)
        if (lineage or {}).get("effect") == EFFECT_DEGRADE:
            match.confidence = row["confidence"]
        elif generated_by == "sector_merger":
            match.confidence = max(match.confidence, row["confidence"])
        if value is not None:
            match.scenario_value = value
        return match, False
    opp = Opportunity(
        firm_id=firm_id,
        ref=next_ref(platform, firm_id, "opportunity", "OP", 3),
        title=row["title"][:255],
        category=category,
        sku=key[:64],
        company_ids=ids,
        confidence=row["confidence"],
        status="New",
        observed_fact=row["detail"],
        evidence=evidence,
        calculation=row.get("calculation", []),
        potential_benefit=row.get("benefit", ""),
        assumptions=row.get("assumptions", []),
        recommended_action=row.get("next_action", ""),
        scenario_value=value,
        generated_by=generated_by,
        lineage=lineage or {},
        synthetic_demo=False,
    )
    platform.add(opp)
    platform.flush()
    return opp, True


def _candidate_row(kind: str, cand: dict) -> dict:
    """A code-screened exact-key join expressed as a proposal awaiting confirmation."""
    companies = ", ".join(cand["companies"])
    if kind == "vendor_consolidation":
        names = "; ".join(f"{c}: {', '.join(n)}" for c, n in cand["names_by_company"].items())
        title, detail = (
            f"Vendor '{cand['shared_key']}' appears at {companies}",
            f"Same supplier key across sister companies ({names}). Consolidation is a candidate pending confirmation of one legal entity.",
        )
    elif kind == "software_overlap":
        prods = "; ".join(f"{c}: {', '.join(p)}" for c, p in cand["products_by_company"].items())
        title, detail = f"'{cand['shared_key']}' software bought at {companies}", f"{cand['note'].capitalize()} ({prods})."
    elif kind == "purchasing_price_gap":
        costs = ", ".join(f"{c} {money2(v)}" for c, v in cand["unit_cost"].items())
        title, detail = (
            f"{cand['shared_key']} unit cost differs by {cand['spread_pct']}% across {companies}",
            f"Unit cost by company: {costs}.",
        )
    elif kind == "cross_sell":
        names = "; ".join(f"{c}: {', '.join(n)}" for c, n in cand["names_by_company"].items())
        title, detail = (
            f"Customer group '{cand['shared_key']}' is served by {companies}",
            f"Shared customer parent ({names}); cross-sell or joint account coverage candidate.",
        )
    else:
        title, detail = (
            f"{kind.replace('_', ' ')}: {cand['shared_key']} across {companies}",
            str({k: v for k, v in cand.items() if k not in ("evidence",)}),
        )
    return {
        "kind": kind,
        "title": title,
        "detail": detail,
        "companies": cand["companies"],
        "shared_key": cand["shared_key"],
        "evidence": cand["evidence"],
        "confidence": SCREEN_CONFIDENCE,
        "estimated_annual_value": None,
    }


@dataclass(frozen=True)
class ReviewerFinding:
    """A structured reviewer finding as the merger sees it: enough to intersect with candidate evidence,
    cite in lineage, and hand to the model as context."""

    id: str
    run_id: str
    company: str
    company_id: str
    title: str
    detail: str
    finding_type: str
    severity: str
    effect: str

    def prompt(self) -> dict:
        return {
            "company": self.company,
            "finding_type": self.finding_type,
            "severity": self.severity,
            "effect": self.effect,
            "title": self.title,
            "detail": self.detail,
        }


# (finding_type, candidate kind) -> effect when the generic effect of a finding type is wrong for a
# particular thesis. Duplicate vendor records make spend estimates less trustworthy in general, but
# they are the vendor-master consolidation thesis itself, so there they add context instead.
EFFECT_OVERRIDES = {
    ("data_quality.duplicate_entity", "vendor_consolidation"): EFFECT_ENRICH,
    ("data_quality.duplicate_entity", "carrier_consolidation"): EFFECT_ENRICH,
}
DEGRADE_FACTOR = Decimal("0.75")
MAX_PROMPT_FINDINGS = 8

FindingIndex = dict[str, dict[str, list[ReviewerFinding]]]  # company short -> canonical id -> findings


def load_reviewer_findings(refs_by_short: dict[str, CompanyRef], run_ids: set[uuid.UUID]) -> tuple[FindingIndex, dict[str, list[str]]]:
    """Structured findings written by the successful reviewer runs, indexed by the canonical ids they are about,
    plus the reviewer run ids found in each company's ledger (a company with no findings still contributes its run
    to lineage). Dismissed findings are invisible to the merger: the human gate between hops is what makes them so."""
    index: FindingIndex = {}
    runs_by_short: dict[str, list[str]] = {}
    if not run_ids:
        return index, runs_by_short
    for short, ref in refs_by_short.items():
        by_id: dict[str, list[ReviewerFinding]] = defaultdict(list)
        with company_session(ref) as ts:
            runs_by_short[short] = sorted(
                str(r) for r in ts.scalars(select(AgentRun.id).where(AgentRun.id.in_(run_ids), AgentRun.run_type == RUN_REVIEW))
            )
            rows = ts.scalars(
                select(Finding).where(
                    Finding.run_id.in_(run_ids),
                    Finding.status.in_(["open", "reviewed"]),
                    Finding.effect.is_not(None),
                )
            ).all()
            for f in rows:
                rf = ReviewerFinding(
                    str(f.id),
                    str(f.run_id),
                    short,
                    str(ref.id),
                    f.title,
                    f.detail,
                    f.finding_type or "",
                    f.severity or "",
                    f.effect or EFFECT_ENRICH,
                )
                for ent in f.affected_entities or []:
                    if ent.get("id"):
                        by_id[str(ent["id"])].append(rf)
        index[short] = dict(by_id)
    return index, runs_by_short


def effect_on(kind: str, finding: ReviewerFinding) -> str:
    return EFFECT_OVERRIDES.get((finding.finding_type, kind), finding.effect)


def validate_candidate(kind: str, cand: dict, index: FindingIndex) -> tuple[str | None, list[tuple[ReviewerFinding, str]]]:
    """Deterministic intersection of a candidate's canonical record ids with the ids reviewer findings
    are about. Returns the strongest effect (block > degrade > enrich, None when nothing intersects)
    and the intersecting findings with their effect on this candidate kind."""
    hits: dict[str, tuple[ReviewerFinding, str]] = {}
    for short, ids in cand.get("record_ids", {}).items():
        by_id = index.get(short, {})
        for rid in ids:
            for f in by_id.get(rid, ()):
                hits.setdefault(f.id, (f, effect_on(kind, f)))
    if not hits:
        return None, []
    ranked = sorted(hits.values(), key=lambda h: (-EFFECT_RANK[h[1]], h[0].company, h[0].title))
    return ranked[0][1], ranked


def _apply_effect(row: dict, effect: str | None, hits: list[tuple[ReviewerFinding, str]]) -> dict:
    """DEGRADE lowers confidence and records the data-quality assumption; ENRICH adds reviewer context.
    Both are applied in code regardless of what the model wrote, so the effect is auditable."""
    if effect is None:
        return row
    assumptions = list(row.get("assumptions") or [])
    for f, eff in hits:
        if eff == EFFECT_DEGRADE:
            assumptions.append(f"Data quality ({f.company}): {f.title}. Estimate treated as less reliable until resolved.")
        elif eff == EFFECT_ENRICH:
            assumptions.append(f"Reviewer context ({f.company}): {f.title}.")
    confidence = row["confidence"]
    if effect == EFFECT_DEGRADE:
        confidence = float((Decimal(str(confidence)) * DEGRADE_FACTOR).quantize(Decimal("0.01")))
    return {**row, "assumptions": assumptions, "confidence": confidence}


def merge_sector(
    platform: Session,
    firm_id: uuid.UUID,
    home_schema: str,
    sector: str,
    company_ids: list[str],
    run_id: uuid.UUID,
    job_id: uuid.UUID,
    parent_run_ids: list[str] | None = None,
    failed_company_ids: list[str] | None = None,
    request_id: str | None = None,
) -> dict:
    """Sector Merger body, three stages over `company_ids` (the companies whose reviews succeeded):
    1. deterministic candidates recomputed from canonical state;
    2. finding-aware validation - each candidate's record ids intersected with the structured findings
       of the reviewer runs in `parent_run_ids` -> BLOCK / DEGRADE / ENRICH;
    3. model interpretation of the surviving candidates with their reviewer context.
    Scope is re-checked against platform.firm_companies; anything outside the firm is dropped with an
    error event rather than failing open, and `failed_company_ids` are recorded as excluded."""
    refs = _authorized(platform, firm_id, company_ids)
    dropped = sorted(set(company_ids) - {str(r.id) for r in refs})
    by_company: dict[Company, list[Table]] = {}
    refs_by_short: dict[str, CompanyRef] = {}
    for ref in refs:
        c = company_for(ref)
        if c.sector != sector:
            continue
        refs_by_short[c.short] = ref
        with company_session(ref) as ts:
            by_company[c] = canonical_tables(ts)
    shorts = set(refs_by_short)
    kinds = [k for k in analyze.SECTOR_KINDS[sector] if k in analyze.SCREENS]
    reviewer_runs = {uuid.UUID(r) for r in (parent_run_ids or [])}
    index, runs_by_company = load_reviewer_findings(refs_by_short, reviewer_runs)
    at = datetime.now(UTC)
    failed = sorted(set(failed_company_ids or []) - set(company_ids))
    blocked = degraded = enriched = 0
    with tenant_session(home_schema) as ts:
        run = _start(ts, run_id)
        run.sector = sector
        seq = _emit(
            ts,
            run_id,
            _next_seq(ts, run_id),
            "step",
            {
                "message": "started",
                "request_id": request_id,
                "parent_run_id": parent_run_ids[0] if parent_run_ids else None,
                "parent_run_ids": sorted(str(r) for r in reviewer_runs),
                "sector": sector,
                "companies": sorted(shorts),
                "failed_company_ids": failed,
                "job_id": str(job_id),
            },
        )
        if dropped:
            seq = _emit(ts, run_id, seq, "error", {"message": "companies outside firm scope dropped", "company_ids": dropped})
        for cid in failed:
            try:
                fc = platform.get(FirmCompany, uuid.UUID(cid))
            except ValueError:
                fc = None
            name = fc.name if fc is not None and fc.firm_id == firm_id else cid
            seq = _emit(
                ts,
                run_id,
                seq,
                "error",
                {"message": f"{name} review failed; {name} excluded from this analysis.", "company_id": cid, "excluded": True},
            )
        seq = _emit(
            ts,
            run_id,
            seq,
            "tool_call",
            {
                "tool": "read_reviewer_findings",
                "runs": len(reviewer_runs),
                "findings": {short: len({f.id for fs in by_id.values() for f in fs}) for short, by_id in index.items()},
            },
        )
        created = updated = 0
        cost = Decimal(0)
        opp_refs: list[str] = []
        for kind in kinds:
            scoped = {c: analyze.tables_for_kind(kind, tables) for c, tables in by_company.items()}
            candidates = analyze.screen(kind, scoped)
            known_refs = {f"{c.short}:{t.ref}" for c, tables in scoped.items() for t in tables}
            seq = _emit(ts, run_id, seq, "tool_call", {"tool": "screen_canonical", "kind": kind, "candidates": len(candidates)})
            validated: list[tuple[dict, str | None, list[tuple[ReviewerFinding, str]]]] = []
            for cand in candidates:
                effect, hits = validate_candidate(kind, cand, index)
                if effect == EFFECT_BLOCK:
                    blocked += 1
                    seq = _emit(
                        ts,
                        run_id,
                        seq,
                        "step",
                        {
                            "message": "candidate blocked by reviewer finding",
                            "kind": kind,
                            "shared_key": cand["shared_key"],
                            "from_findings": [f.id for f, eff in hits if eff == EFFECT_BLOCK],
                        },
                    )
                    continue
                degraded += effect == EFFECT_DEGRADE
                enriched += effect == EFFECT_ENRICH
                prompt_cand = {**cand, "reviewer_findings": [f.prompt() for f, _ in hits[:MAX_PROMPT_FINDINGS]]} if hits else cand
                validated.append((prompt_cand, effect, hits))
            if not validated:
                continue
            phase = run_phase(
                analyze.prepare(sector, scoped, kind, [v[0] for v in validated]),
                lambda text, k=kind: analyze.parse(text, k),
                lambda out, r=known_refs: analyze.apply(out, shorts, r),
                llm=chat,
            )
            r = phase.result
            seq = _emit(
                ts,
                run_id,
                seq,
                "model_call",
                {"kind": kind, "model": r.model, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "source": r.source},
            )
            cost += _usage(ts, run, r)
            confirmed = {row["shared_key"].strip().lower(): row for row in phase.rows}
            rejected = {str(x).lower() for x in phase.output.rejected}
            for cand, effect, hits in validated:
                key = cand["shared_key"].strip().lower()
                if any(key in x for x in rejected):
                    seq = _emit(ts, run_id, seq, "step", {"message": "look-alike rejected", "kind": kind, "shared_key": cand["shared_key"]})
                    continue
                row = _apply_effect(confirmed.get(key) or _candidate_row(kind, cand), effect, hits)
                generated_by = "sector_merger" if key in confirmed else "deterministic_screen"
                record_ids = cand.get("record_ids", {})
                evidence = [
                    {
                        "companyId": str(refs_by_short[s].id),
                        "entity": t.rsplit("/", 1)[-1],
                        "table": t,
                        "recordIds": record_ids.get(s, [])[:MAX_EVIDENCE_IDS],
                        "recordCount": len(record_ids.get(s, [])),
                    }
                    for s in row["companies"]
                    for t in [e.split(":", 1)[1] for e in cand["evidence"] if e.startswith(f"{s}:")]
                ]
                lineage = {
                    "request_id": request_id,
                    "merge_run_id": str(run_id),
                    "effect": effect,
                    "from_findings": sorted(f.id for f, _ in hits),
                    "from_runs": sorted({r for s in row["companies"] for r in runs_by_company.get(s, [])} | {f.run_id for f, _ in hits}),
                    "reviewer_findings": [
                        {
                            "id": f.id,
                            "company": f.company,
                            "companyId": f.company_id,
                            "finding_type": f.finding_type,
                            "effect": eff,
                            "title": f.title,
                        }
                        for f, eff in hits
                    ],
                    "failed_company_ids": failed,
                }
                opp, is_new = _upsert_opportunity(platform, firm_id, row, refs_by_short, evidence, generated_by, lineage)
                created += is_new
                updated += not is_new
                opp_refs.append(opp.ref)
                f, _ = _upsert_finding(
                    ts,
                    run,
                    "proposed_automation",
                    f"{kind}: {row['title']}",
                    row["detail"],
                    {
                        "sector": sector,
                        "opportunity_kind": kind,
                        "opportunity_ref": opp.ref,
                        "companies": row["companies"],
                        "shared_key": row["shared_key"],
                        "refs": cand["evidence"],
                        "confidence": row["confidence"],
                        "generated_by": generated_by,
                        "effect": effect,
                        "from_findings": lineage["from_findings"],
                        "from_runs": lineage["from_runs"],
                    },
                )
                seq = _emit(
                    ts,
                    run_id,
                    seq,
                    "finding",
                    {
                        "finding_id": str(f.id),
                        "opportunity": opp.ref,
                        "title": f.title,
                        "effect": effect,
                        "from_findings": lineage["from_findings"],
                    },
                )
                if is_new:
                    log_activity(
                        platform,
                        firm_id,
                        uuid.UUID(opp.company_ids[0]),
                        f"Sector Merger proposed {opp.ref}: {opp.title}",
                        "opportunity",
                        at,
                    )
        _finish(
            ts,
            run,
            seq,
            {
                "opportunities_created": created,
                "opportunities_updated": updated,
                "blocked": blocked,
                "degraded": degraded,
                "enriched": enriched,
                "excluded_company_ids": failed,
                "cost_usd": str(cost),
            },
        )
        ts.commit()
    for ref in refs_by_short.values():
        with company_session(ref) as ts:
            agent = _workspace_agent(
                ts,
                ref,
                f"sector-merger-{ref.slug}",
                "Sector Merger",
                f"Cross-company comparison across the firm's {sector.replace('_', ' ')} companies",
            )
            n = len(ts.scalars(select(WorkspaceAgentRun.id).where(WorkspaceAgentRun.agent_id == agent.id)).all())
            ts.add(
                WorkspaceAgentRun(
                    company_id=ref.id,
                    agent_id=agent.id,
                    ref=f"run-{agent.ref}-{n + 1:04d}",
                    status="Complete",
                    started_at=at,
                    model_cost=(cost / max(len(refs_by_short), 1)).quantize(Decimal("0.0001")),
                    payload={
                        "goal": f"Join canonical tables across {', '.join(sorted(shorts))} on exact shared keys.",
                        "sources": [f"canonical/{t}" for k in kinds for t in sorted(analyze.KIND_TABLES[k])],
                        "events": [
                            [at.strftime("%H:%M"), f"{created} new and {updated} updated opportunities: {', '.join(opp_refs) or 'none'}"]
                        ],
                        "output": f"{created + updated} cross-company opportunities proposed for the {sector.replace('_', ' ')} sector.",
                        "evidence": [],
                        "corrections": [],
                        "ledger_run_id": str(run_id),
                    },
                )
            )
            agent.last_run_at = at
            agent.payload = {
                **(agent.payload or {}),
                "cases": (agent.payload or {}).get("cases", 0) + created + updated,
                "findings": created + updated,
                "cost": round(float((agent.payload or {}).get("cost", 0)) + float(cost) / max(len(refs_by_short), 1), 4),
            }
            ts.commit()
    for ref in refs_by_short.values():
        platform.get(FirmCompany, ref.id).analysis_run_at = at
    return {
        "sector": sector,
        "companies": sorted(shorts),
        "created": created,
        "updated": updated,
        "blocked": blocked,
        "degraded": degraded,
        "enriched": enriched,
        "dropped": dropped,
        "excluded": failed,
        "cost_usd": str(cost),
    }


# ---- Job handlers + orchestration --------------------------------------------------------------


def _ctx(platform: Session, firm_id: str, user_id: str) -> FirmContext:
    """Consumer-side scope check: the job carries ids only; the firm context is
    rebuilt from the requesting user and must resolve to the firm named in the payload."""
    user = platform.get(User, uuid.UUID(user_id))
    if user is None:
        raise RuntimeError("requesting user not found")
    tenant = platform.get(Tenant, user.tenant_id)
    ctx = load_firm_context(
        platform, Principal(user_id=user.id, tenant_id=tenant.id, tenant_schema=tenant.schema_name, email=user.email, role=user.role)
    )
    if str(ctx.firm.id) != firm_id:
        raise RuntimeError("job firm does not match the requesting user's firm")
    return ctx


def handle_canonical_review(job: Job, tenant_schema: str) -> None:
    payload = job.payload
    with platform_session() as platform:
        ctx = _ctx(platform, payload["firm_id"], payload["requested_by"])
        company = ctx.company(payload["company_id"])
        if company.schema != tenant_schema:
            raise RuntimeError("job tenant does not match the company tenant")
        review_company(platform, ctx, company, uuid.UUID(payload["run_id"]), job.id, payload.get("parent_run_id"))
        platform.commit()


def handle_portfolio_merge(job: Job, tenant_schema: str) -> None:
    payload = job.payload
    with platform_session() as platform:
        ctx = _ctx(platform, payload["firm_id"], payload["requested_by"])
        home = platform.get(Tenant, ctx.firm.home_tenant_id)
        if home is None or home.schema_name != tenant_schema:
            raise RuntimeError("merge job tenant is not the firm's home tenant")
        merge_sector(
            platform,
            ctx.firm.id,
            tenant_schema,
            payload["sector"],
            payload.get("successful_company_ids", payload["company_ids"]),
            uuid.UUID(payload["run_id"]),
            job.id,
            payload.get("successful_run_ids", payload.get("parent_run_ids")),
            payload.get("failed_company_ids"),
            payload.get("request_id"),
        )
        platform.commit()


HANDLERS = {RUN_REVIEW: handle_canonical_review, RUN_MERGE: handle_portfolio_merge}
TERMINAL_JOB_STATUS = {"succeeded", "failed"}
MERGE_WAITING = "waiting"  # status the UI sees for a sector merge whose barrier has not opened yet


def _sector_members(ctx: FirmContext, sector: str) -> list[CompanyRef]:
    return [c for c in ctx.companies if company_for(c).sector == sector]


def _merge_job(platform: Session, home_tenant_id: uuid.UUID, request_id: str, sector: str) -> Job | None:
    return platform.scalar(
        select(Job).where(
            Job.tenant_id == home_tenant_id, Job.kind == RUN_MERGE, Job.idempotency_key == f"{RUN_MERGE}:{request_id}:{sector}"
        )
    )


def release_sector_barrier(platform: Session, ctx: FirmContext, request_id: str, sector: str) -> Job | None:
    """The barrier between hop 1 and hop 2. Once every company review job of `sector` for this request
    is terminal (succeeded or permanently failed), queue the sector's `portfolio_merge` job exactly once,
    scoped to the companies whose reviews succeeded and naming the ones that failed. Returns the merge
    job when it exists after the call (created now or earlier), None while the barrier is still closed.

    Sibling review jobs are read under row locks so two reviewers finishing at the same moment cannot
    both see the barrier open and race; the stable idempotency key makes a second pass a no-op anyway."""
    members = _sector_members(ctx, sector)
    if len(members) < 2:
        return None
    home = platform.get(Tenant, ctx.firm.home_tenant_id)
    existing = _merge_job(platform, home.id, request_id, sector)
    if existing is not None:
        return existing
    keys = {f"{RUN_REVIEW}:{request_id}:{m.id}": m for m in members}
    siblings = platform.scalars(
        select(Job)
        .where(Job.kind == RUN_REVIEW, Job.tenant_id.in_([m.tenant_id for m in members]), Job.idempotency_key.in_(list(keys)))
        .with_for_update()
    ).all()
    by_key = {j.idempotency_key: j for j in siblings}
    if len(by_key) < len(keys) or any(j.status not in TERMINAL_JOB_STATUS for j in by_key.values()):
        return None
    existing = _merge_job(platform, home.id, request_id, sector)  # re-check under the locks
    if existing is not None:
        return existing
    merge_run_id = next((j.payload.get("merge_run_id") for j in by_key.values() if j.payload.get("merge_run_id")), None)
    if merge_run_id is None:
        return None
    succeeded = [(m, by_key[k]) for k, m in keys.items() if by_key[k].status == "succeeded"]
    failed = [m for k, m in keys.items() if by_key[k].status == "failed"]
    requested_by = next(iter(by_key.values())).payload["requested_by"]
    job = enqueue(
        platform,
        home.id,
        RUN_MERGE,
        {
            "run_id": merge_run_id,
            "request_id": request_id,
            "firm_id": str(ctx.firm.id),
            "sector": sector,
            "requested_by": requested_by,
            "company_ids": [str(m.id) for m in members],
            "successful_company_ids": [str(m.id) for m, _ in succeeded],
            "failed_company_ids": [str(m.id) for m in failed],
            "successful_run_ids": [j.payload["run_id"] for _, j in succeeded],
            "parent_run_ids": [j.payload["run_id"] for _, j in succeeded],
        },
        idempotency_key=f"{RUN_MERGE}:{request_id}:{sector}",
    )
    platform.flush()
    with tenant_session(home.schema_name) as ts:
        ts.execute(update(AgentRun).where(AgentRun.id == uuid.UUID(merge_run_id)).values(job_id=job.id))
        ts.commit()
    for m, j in succeeded:
        with company_session(m) as ts:
            run_id = uuid.UUID(j.payload["run_id"])
            _emit(
                ts,
                run_id,
                _next_seq(ts, run_id),
                "handoff",
                {
                    "to": RUN_MERGE,
                    "run_id": merge_run_id,
                    "job_id": str(job.id),
                    "sector": sector,
                    "failed_company_ids": [str(x.id) for x in failed],
                },
            )
            ts.commit()
    return job


def after_review_terminal(job_id: uuid.UUID) -> None:
    """Worker hook, called after a `canonical_review` job's terminal status is committed."""
    with platform_session() as platform:
        job = platform.get(Job, job_id)
        if job is None or job.kind != RUN_REVIEW or job.status not in TERMINAL_JOB_STATUS:
            return
        payload = job.payload
        if not payload.get("request_id") or not payload.get("sector"):
            return
        ctx = _ctx(platform, payload["firm_id"], payload["requested_by"])
        release_sector_barrier(platform, ctx, payload["request_id"], payload["sector"])
        platform.commit()


AFTER_TERMINAL = {RUN_REVIEW: after_review_terminal}


def _queued_request(
    platform: Session, ctx: FirmContext, home: Tenant, request_id: uuid.UUID, by_sector: dict[str, list[CompanyRef]]
) -> dict | None:
    """The report for a request that has already been queued (every review job exists under its
    stable key), or None if this request has not been seen. Re-running a request must not mint new
    envelopes for jobs that already point at the old ones. Merge jobs are created by the barrier, so a
    sector whose reviews are not all terminal reports None for its merge job; if they are, the barrier
    is given a chance to open here as well (covers a worker that died between commit and hook)."""

    def job_id(tenant_id: uuid.UUID, kind: str, key: str) -> str | None:
        found = platform.scalar(select(Job.id).where(Job.tenant_id == tenant_id, Job.kind == kind, Job.idempotency_key == key))
        return str(found) if found else None

    review = {str(c.id): job_id(c.tenant_id, RUN_REVIEW, f"{RUN_REVIEW}:{request_id}:{c.id}") for c in ctx.companies}
    if not review or any(v is None for v in review.values()):
        return None
    merge: dict[str, str | None] = {}
    for sector, members in by_sector.items():
        if len(members) < 2:
            continue
        job = release_sector_barrier(platform, ctx, str(request_id), sector)
        merge[sector] = str(job.id) if job is not None else None
    return {"request_id": str(request_id), "review": review, "merge": merge}


def interpretation_status(platform: Session, ctx: FirmContext, request_id: uuid.UUID) -> dict:
    """Progress of one analyst request: every job queued under its stable keys, restricted to
    tenants in the caller's firm scope (company tenants + the firm's home tenant), plus one
    `waiting` entry per sector merge whose barrier has not opened yet. `done` is true once every
    hop is terminal and no merge is waiting."""
    tenant_ids = {c.tenant_id for c in ctx.companies} | {ctx.firm.home_tenant_id}
    jobs = platform.scalars(
        select(Job)
        .where(
            Job.tenant_id.in_(tenant_ids),
            Job.kind.in_(list(HANDLERS)),
            Job.idempotency_key.like(f"%:{request_id}:%"),
        )
        .order_by(Job.created_at)
    ).all()
    if not jobs:
        raise HTTPException(404, "Unknown interpretation request")
    views = [
        {
            "id": str(j.id),
            "kind": j.kind,
            "scope": j.idempotency_key.rsplit(":", 1)[-1],
            "status": j.status,
            "attempts": j.attempts,
            "error": j.error,
        }
        for j in jobs
    ]
    merged = {v["scope"] for v in views if v["kind"] == RUN_MERGE}
    waiting_on: dict[str, list[str]] = defaultdict(list)
    for j in jobs:
        sector = j.payload.get("sector")
        if j.kind == RUN_REVIEW and sector and j.payload.get("merge_run_id") and sector not in merged:
            if j.status not in TERMINAL_JOB_STATUS:
                waiting_on[sector].append(j.payload["company_id"])
            waiting_on.setdefault(sector, [])
    for sector, pending in sorted(waiting_on.items()):
        views.append(
            {"id": None, "kind": RUN_MERGE, "scope": sector, "status": MERGE_WAITING, "attempts": 0, "error": None, "waiting_on": pending}
        )
    return {
        "request_id": str(request_id),
        "jobs": views,
        "done": all(v["status"] in TERMINAL_JOB_STATUS for v in views),
        "succeeded": sum(v["status"] == "succeeded" for v in views),
        "failed": sum(v["status"] == "failed" for v in views),
    }


def run_portfolio_interpretation(platform: Session, ctx: FirmContext, request_id: uuid.UUID | None = None) -> dict:
    """Queue the interpretation chain for one analyst request: a File Reviewer run per company
    (hop 1) as durable jobs, and a Sector Merger run envelope per sector (hop 2) whose job is
    created by `release_sector_barrier` once every review in that sector is terminal. Every hop is
    an AgentRun + durable job for `python -m vista.jobs.worker`; nothing runs inline.

    `request_id` identifies the analyst request and is the root of the lineage, so idempotency
    keys are `f"{kind}:{request_id}:{scope}"`: re-queueing the same request is a no-op, while a new
    request gets fresh runs. Returns the queued job ids keyed by target; a sector's merge job id is
    None until its barrier opens."""
    request_id = request_id or uuid.uuid4()
    report: dict = {"request_id": str(request_id), "review": {}, "merge": {}}
    home = platform.get(Tenant, ctx.firm.home_tenant_id)
    requested_by = str(ctx.principal.user_id)
    by_sector: dict[str, list[CompanyRef]] = defaultdict(list)
    for company in ctx.companies:
        by_sector[company_for(company).sector].append(company)

    queued = _queued_request(platform, ctx, home, request_id, by_sector)
    if queued is not None:
        return queued

    # Hop-2 envelopes (Sector Merger in the firm's home tenant): queued AgentRuns without a job,
    # so the UI can show the merge waiting on its reviewers.
    merge_run_ids: dict[str, uuid.UUID] = {}
    with tenant_session(home.schema_name) as ts:
        for sector, members in by_sector.items():
            if len(members) < 2:
                continue
            run = AgentRun(
                job_id=uuid.uuid4(),
                run_type=RUN_MERGE,
                requested_by=ctx.principal.user_id,
                agent_key=agent_key_for(RUN_MERGE),
                sector=sector,
            )
            ts.add(run)
            ts.flush()
            merge_run_ids[sector] = run.id
            report["merge"][sector] = None
        ts.commit()

    # Hop-1 envelopes + jobs (File Reviewer, one per company tenant). Each review job knows the
    # sector merge it feeds so the barrier can find the envelope.
    for company in ctx.companies:
        c = company_for(company)
        with company_session(company) as ts:
            run = AgentRun(
                job_id=uuid.uuid4(),
                run_type=RUN_REVIEW,
                requested_by=ctx.principal.user_id,
                agent_key=agent_key_for(RUN_REVIEW),
                company=c.short,
                sector=c.sector,
            )
            ts.add(run)
            ts.commit()
            run_id = run.id
        job = enqueue(
            platform,
            company.tenant_id,
            RUN_REVIEW,
            {
                "run_id": str(run_id),
                "parent_run_id": None,
                "request_id": str(request_id),
                "firm_id": str(ctx.firm.id),
                "company_id": str(company.id),
                "sector": c.sector,
                "merge_run_id": str(merge_run_ids[c.sector]) if c.sector in merge_run_ids else None,
                "requested_by": requested_by,
            },
            idempotency_key=f"{RUN_REVIEW}:{request_id}:{company.id}",
        )
        with company_session(company) as ts:
            ts.execute(update(AgentRun).where(AgentRun.id == run_id).values(job_id=job.id))
            ts.commit()
        report["review"][str(company.id)] = str(job.id)
    platform.commit()
    return report
