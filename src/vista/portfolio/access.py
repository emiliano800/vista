"""Firm-scoped authorisation. A principal may only see companies that are
explicitly linked to a firm they belong to (platform.firm_companies)."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from vista.auth import Principal, current_principal
from vista.config import settings
from vista.db import platform_session, tenant_session
from vista.models.platform import Firm, FirmCompany, FirmMembership, Tenant

WRITE_ROLES = {"analyst", "operator", "admin"}


def today() -> date:
    if settings.use_synthetic_data and settings.demo_today:
        return date.fromisoformat(settings.demo_today)
    return datetime.now().date()


@dataclass
class ReportingPeriod:
    start: date
    end: date
    label: str


def reporting_period(anchor: date | None = None) -> ReportingPeriod:
    """Trailing twelve months ending with the anchor's month."""
    anchor = anchor or today()
    last = (anchor.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    start_year, start_month = (anchor.year - 1, anchor.month + 1) if anchor.month < 12 else (anchor.year, 1)
    start = date(start_year, start_month, 1)
    return ReportingPeriod(start, last, f"TTM ending {anchor.strftime('%b')}. {anchor.year}")


@dataclass
class CompanyRef:
    id: uuid.UUID
    slug: str
    name: str
    tenant_id: uuid.UUID
    schema: str
    row: FirmCompany

    @property
    def deal_id(self) -> uuid.UUID | None:
        """The Deal inside the company tenant that deal-scoped routes (company workspace,
        recorder) use for the same company."""
        return self.row.deal_id


@dataclass
class FirmContext:
    """Who is acting, for which firm, over which companies. `membership` is the firm
    membership behind an analyst request; it is None when the scope was resolved from a
    company tenant instead (an employee acting inside their own company), in which case
    deal roles, not firm roles, decide what they may write."""

    principal: Principal
    firm: Firm
    membership: FirmMembership | None
    companies: list[CompanyRef]

    @property
    def can_write(self) -> bool:
        return self.membership is not None and self.membership.role in WRITE_ROLES

    @property
    def actor(self) -> str:
        return (self.membership.display_name if self.membership else None) or self.principal.email

    def company(self, key: str | uuid.UUID) -> CompanyRef:
        text = str(key)
        for c in self.companies:
            if text == str(c.id) or text == c.slug:
                return c
        raise HTTPException(404, "Company not found")

    def company_ids(self) -> list[str]:
        return [str(c.id) for c in self.companies]


def load_firm_context(session: Session, principal: Principal) -> FirmContext:
    row = session.execute(
        select(FirmMembership, Firm).join(Firm, Firm.id == FirmMembership.firm_id).where(FirmMembership.user_id == principal.user_id)
    ).first()
    if row is None:
        raise HTTPException(403, "This account is not a member of a PE firm")
    membership, firm = row
    return FirmContext(principal, firm, membership, _company_refs(session, firm.id))


def _company_refs(session: Session, firm_id: uuid.UUID) -> list[CompanyRef]:
    return [
        CompanyRef(fc.id, fc.slug, fc.name, fc.tenant_id, schema, fc)
        for fc, schema in session.execute(
            select(FirmCompany, Tenant.schema_name)
            .join(Tenant, Tenant.id == FirmCompany.tenant_id)
            .where(FirmCompany.firm_id == firm_id)
            .order_by(FirmCompany.acquisition_date.nulls_last(), FirmCompany.created_at)
        ).all()
    ]


def firm_scope(session: Session, firm: Firm, principal: Principal) -> FirmContext:
    """The firm's full company scope without a membership: for work the platform does on
    the firm's behalf (a queued review) or for an employee acting inside one company."""
    return FirmContext(principal, firm, None, _company_refs(session, firm.id))


def company_context(session: Session, principal: Principal, deal_id: uuid.UUID) -> FirmContext:
    """Scope for a company workspace user: their own tenant must be a portfolio company
    (platform.firm_companies) and `deal_id` must be that company's Deal. The context
    carries exactly one company; the caller still checks the user's deal role."""
    row = session.execute(
        select(FirmCompany, Firm, Tenant.schema_name)
        .join(Firm, Firm.id == FirmCompany.firm_id)
        .join(Tenant, Tenant.id == FirmCompany.tenant_id)
        .where(FirmCompany.tenant_id == principal.tenant_id)
    ).first()
    if row is None or row[0].deal_id != deal_id:
        raise HTTPException(409, "This workspace is not linked to a portfolio company yet; ask your Vista contact to finish onboarding.")
    fc, firm, schema = row
    return FirmContext(principal, firm, None, [CompanyRef(fc.id, fc.slug, fc.name, fc.tenant_id, schema, fc)])


def firm_context(principal: Principal = Depends(current_principal)) -> FirmContext:
    with platform_session() as session:
        ctx = load_firm_context(session, principal)
        session.expunge_all()
    return ctx


def writer_context(ctx: FirmContext = Depends(firm_context)) -> FirmContext:
    if not ctx.can_write:
        raise HTTPException(403, "Viewers cannot change portfolio data")
    return ctx


@contextmanager
def company_session(company: CompanyRef):
    with tenant_session(company.schema) as session:
        yield session
