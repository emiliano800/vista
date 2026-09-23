"""Company workspace imports: the same canonical import contract the analyst uses
(`vista.portfolio.imports` — detect, map, preview, exceptions, approve into canonical
rows with row-level provenance), reached from inside one company by its Deal.

Scope is the employee's deal role (`viewer` reads, `member`/`owner` write) on the Deal
that `platform.firm_companies.deal_id` names for their tenant. Nothing here has a
second storage path: an import approved here is the import the analyst sees."""

from __future__ import annotations

import base64
import binascii
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from vista.auth import Principal, current_principal
from vista.db import platform_session, tenant_session
from vista.models.tenant import ImportJob
from vista.permissions import require_deal_role
from vista.portfolio import imports as import_service
from vista.portfolio import interpret
from vista.portfolio.access import CompanyRef, FirmContext, company_context, company_session
from vista.portfolio.processors import DATASETS
from vista.portfolio.state import company_imports, company_records

router = APIRouter(tags=["company imports"])


def _scope(deal_id: uuid.UUID, principal: Principal, minimum: str) -> tuple[FirmContext, CompanyRef, str]:
    """(context, company, role) after the deal-role check inside the caller's own tenant."""
    with tenant_session(principal.tenant_schema) as ts:
        role = require_deal_role(ts, deal_id, principal.user_id, minimum)
    with platform_session() as platform:
        ctx = company_context(platform, principal, deal_id)
        platform.expunge_all()
    return ctx, ctx.companies[0], role


def _job_in(company: CompanyRef, job_id: uuid.UUID) -> None:
    """Import jobs are addressed under the company that owns them; a job from anywhere else is unknown here."""
    with company_session(company) as ts:
        if ts.scalar(select(ImportJob.id).where(ImportJob.id == job_id)) is None:
            raise HTTPException(404, "Import job not found")


@router.get("/deals/{deal_id}/import-datasets")
def datasets(deal_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> dict:
    _scope(deal_id, principal, "viewer")
    return {
        key: {
            "label": d["label"],
            "entity": d.get("entity"),
            "fields": {f: {"label": spec["label"], "required": bool(spec.get("required"))} for f, spec in d.get("fields", {}).items()},
        }
        for key, d in DATASETS.items()
    }


@router.get("/deals/{deal_id}/imports")
def list_imports(deal_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> dict:
    _ctx, company, role = _scope(deal_id, principal, "viewer")
    with company_session(company) as ts:
        jobs, open_exceptions = company_imports(ts)
    return {
        "role": role,
        "company": {"id": str(company.id), "name": company.name, "slug": company.slug},
        "imports": jobs,
        "openExceptions": open_exceptions,
    }


@router.get("/deals/{deal_id}/records")
def records(deal_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> dict:
    """Every canonical record of this company with its provenance — the rows an approved import wrote."""
    _ctx, company, _role = _scope(deal_id, principal, "viewer")
    with company_session(company) as ts:
        return company_records(ts)


class ImportUpload(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=11_000_000, repr=False)
    mime_type: str = Field(default="", max_length=128)


@router.post("/deals/{deal_id}/imports", status_code=201)
def upload_import(deal_id: uuid.UUID, body: ImportUpload, principal: Principal = Depends(current_principal)) -> dict:
    ctx, company, _role = _scope(deal_id, principal, "member")
    try:
        content = base64.b64decode(body.content, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(422, "Invalid file encoding") from exc
    filename = body.name.replace("\\", "/").rsplit("/", 1)[-1]
    if not filename:
        raise HTTPException(422, "A filename is required")
    with platform_session() as session:
        job = import_service.create_import(session, ctx, company, filename, content, body.mime_type)
    return import_service.job_view(company, job.id)


@router.get("/deals/{deal_id}/imports/{job_id}")
def get_import(deal_id: uuid.UUID, job_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> dict:
    _ctx, company, _role = _scope(deal_id, principal, "viewer")
    return import_service.job_view(company, job_id)


class DatasetChange(BaseModel):
    dataset: str = Field(max_length=32)


@router.post("/deals/{deal_id}/imports/{job_id}/dataset")
def change_dataset(deal_id: uuid.UUID, job_id: uuid.UUID, body: DatasetChange, principal: Principal = Depends(current_principal)) -> dict:
    ctx, company, _role = _scope(deal_id, principal, "member")
    _job_in(company, job_id)
    import_service.set_dataset(ctx, company, job_id, body.dataset)
    return import_service.job_view(company, job_id)


@router.get("/deals/{deal_id}/imports/{job_id}/preview")
def preview_import(deal_id: uuid.UUID, job_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> list[dict]:
    ctx, company, _role = _scope(deal_id, principal, "viewer")
    _job_in(company, job_id)
    return import_service.preview(ctx, company, job_id)


class MappingDecision(BaseModel):
    source: str = Field(max_length=255)
    target: str | None = Field(default=None, max_length=64)
    confirmed: bool = False


class MappingApproval(BaseModel):
    mappings: list[MappingDecision] = Field(default_factory=list, max_length=200)


@router.post("/deals/{deal_id}/imports/{job_id}/mappings/approve")
def approve_mappings(
    deal_id: uuid.UUID, job_id: uuid.UUID, body: MappingApproval, principal: Principal = Depends(current_principal)
) -> dict:
    ctx, company, _role = _scope(deal_id, principal, "member")
    _job_in(company, job_id)
    with platform_session() as session:
        import_service.approve_mappings(session, ctx, company, job_id, [m.model_dump() for m in body.mappings])
    return import_service.job_view(company, job_id)


class ExceptionDecision(BaseModel):
    decision: str | None = Field(default=None, max_length=32)


@router.post("/deals/{deal_id}/imports/{job_id}/exceptions/{exception_ref}")
def decide_exception(
    deal_id: uuid.UUID, job_id: uuid.UUID, exception_ref: str, body: ExceptionDecision, principal: Principal = Depends(current_principal)
) -> dict:
    ctx, company, _role = _scope(deal_id, principal, "member")
    _job_in(company, job_id)
    with platform_session() as session:
        import_service.resolve_exception(session, ctx, company, job_id, exception_ref, body.decision)
    return import_service.job_view(company, job_id)


@router.post("/deals/{deal_id}/imports/{job_id}/approve")
def approve_import(deal_id: uuid.UUID, job_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> dict:
    ctx, company, _role = _scope(deal_id, principal, "member")
    _job_in(company, job_id)
    with platform_session() as session:
        import_service.approve_import(session, ctx, company, job_id)
    return import_service.job_view(company, job_id)


@router.post("/deals/{deal_id}/review", status_code=202)
def run_review(deal_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> dict:
    """Queue the File Reviewer over this company's canonical rows (the same durable job the
    analyst's portfolio analysis runs per company). Findings land in the shared ledger."""
    ctx, company, _role = _scope(deal_id, principal, "member")
    with platform_session() as session:
        return interpret.queue_company_review(session, ctx, company)
