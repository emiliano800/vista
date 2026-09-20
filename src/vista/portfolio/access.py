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


@dataclass
class FirmContext:
    principal: Principal
    firm: Firm
    membership: FirmMembership
    companies: list[CompanyRef]

    @property
    def can_write(self) -> bool:
        return self.membership.role in WRITE_ROLES

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
    companies = [
        CompanyRef(fc.id, fc.slug, fc.name, fc.tenant_id, schema, fc)
        for fc, schema in session.execute(
            select(FirmCompany, Tenant.schema_name)
            .join(Tenant, Tenant.id == FirmCompany.tenant_id)
            .where(FirmCompany.firm_id == firm.id)
            .order_by(FirmCompany.acquisition_date.nulls_last(), FirmCompany.created_at)
        ).all()
    ]
    return FirmContext(principal, firm, membership, companies)


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
