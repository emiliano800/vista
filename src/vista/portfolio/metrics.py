"""Company metrics computed from canonical tables with SQL aggregates. Nothing
here is hard-coded: replacing synthetic rows with imported rows changes the
numbers automatically."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from vista.models.tenant import (
    Customer,
    InventoryBalance,
    Invoice,
    Policy,
    PurchaseOrder,
    PurchaseOrderLine,
    Subscription,
    Vendor,
    VendorPurchase,
)
from vista.portfolio.access import ReportingPeriod

OPEN_INVOICE_STATUSES = ("open", "overdue", "disputed")


@dataclass
class CompanyMetrics:
    customers: int
    invoiceCount: int
    revenue: float
    outstandingAr: float
    outstandingInvoiceCount: int
    overdueAr: float
    overdueInvoiceCount: int
    overdue90Count: int
    overdue90Ar: float
    oldestOverdue90DueDate: str | None
    vendorSpend: float
    vendorCount: int
    softwareAnnual: float
    subscriptionCount: int
    policyCount: int
    policyPremium: float
    policiesExpiring90: int
    purchaseOrderCount: int
    purchaseOrderSpend: float
    openPoLines: int
    inventoryItems: int
    inventoryValue: float

    def as_dict(self) -> dict:
        return asdict(self)


def _f(v) -> float:
    return round(float(v or 0), 2)


def company_metrics(session: Session, period: ReportingPeriod, today: date) -> CompanyMetrics:
    customers = session.scalar(select(func.count()).select_from(Customer)) or 0
    inv_count, revenue = session.execute(
        select(func.count(), func.coalesce(func.sum(Invoice.amount), 0)).where(Invoice.issue_date.between(period.start, period.end))
    ).one()
    outstanding = Invoice.outstanding_balance
    open_ar, open_count = session.execute(
        select(func.coalesce(func.sum(outstanding), 0), func.count()).where(Invoice.status.in_(OPEN_INVOICE_STATUSES), outstanding > 0)
    ).one()
    overdue_ar, overdue_count = session.execute(
        select(func.coalesce(func.sum(outstanding), 0), func.count()).where(Invoice.due_date < today, outstanding > 0)
    ).one()
    cutoff = today - timedelta(days=90)
    over90_ar, over90_count, oldest = session.execute(
        select(func.coalesce(func.sum(outstanding), 0), func.count(), func.min(Invoice.due_date)).where(
            Invoice.due_date < cutoff, outstanding > 0
        )
    ).one()
    spend = session.scalar(
        select(func.coalesce(func.sum(VendorPurchase.total_amount), 0)).where(
            VendorPurchase.purchase_date.between(period.start, period.end)
        )
    )
    vendor_count = session.scalar(select(func.count()).select_from(Vendor)) or 0
    software, sub_count = session.execute(
        select(
            func.coalesce(
                func.sum(case((Subscription.annual_cost > 0, Subscription.annual_cost), else_=Subscription.monthly_cost * 12)), 0
            ),
            func.count(),
        )
    ).one()
    policy_count, premium, expiring = session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(Policy.annual_premium), 0),
            func.coalesce(func.sum(case((Policy.expiration_date.between(today, today + timedelta(days=90)), 1), else_=0)), 0),
        )
    ).one()
    po_count, po_spend = session.execute(
        select(func.count(), func.coalesce(func.sum(PurchaseOrder.total_amount), 0)).where(
            PurchaseOrder.po_date.between(period.start, period.end)
        )
    ).one()
    open_lines = session.scalar(
        select(func.count()).select_from(PurchaseOrderLine).where(PurchaseOrderLine.received_qty < PurchaseOrderLine.ordered_qty)
    )
    inv_items, inv_value = session.execute(
        select(func.count(), func.coalesce(func.sum(InventoryBalance.extended_value), 0)).where(InventoryBalance.on_hand_qty > 0)
    ).one()
    return CompanyMetrics(
        customers=customers,
        invoiceCount=inv_count,
        revenue=_f(revenue),
        outstandingAr=_f(open_ar),
        outstandingInvoiceCount=open_count,
        overdueAr=_f(overdue_ar),
        overdueInvoiceCount=overdue_count,
        overdue90Count=over90_count,
        overdue90Ar=_f(over90_ar),
        oldestOverdue90DueDate=oldest.isoformat() if oldest else None,
        vendorSpend=_f(spend),
        vendorCount=vendor_count,
        softwareAnnual=_f(software),
        subscriptionCount=sub_count,
        policyCount=policy_count,
        policyPremium=_f(premium),
        policiesExpiring90=int(expiring),
        purchaseOrderCount=po_count,
        purchaseOrderSpend=_f(po_spend),
        openPoLines=open_lines or 0,
        inventoryItems=inv_items,
        inventoryValue=_f(inv_value),
    )


def portfolio_metrics(rows: list[CompanyMetrics]) -> dict:
    return {
        "companies": len(rows),
        "revenue": _f(sum(r.revenue for r in rows)),
        "outstandingAr": _f(sum(r.outstandingAr for r in rows)),
        "overdueAr": _f(sum(r.overdueAr for r in rows)),
        "vendorSpend": _f(sum(r.vendorSpend for r in rows)),
        "customers": sum(r.customers for r in rows),
    }
