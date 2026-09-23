"""PE analyst API: portfolio, company workspace, imports, opportunities, tasks
and finding triage. Every handler resolves the caller's firm first and only
ever touches companies linked to that firm."""

from __future__ import annotations

import base64
import binascii
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from vista.db import platform_session
from vista.models.tenant import RecorderReport
from vista.portfolio import imports as import_service
from vista.portfolio import interpret, service
from vista.portfolio import serializers as ser
from vista.portfolio.access import FirmContext, company_session, firm_context, writer_context
from vista.portfolio.processors import DATASETS
from vista.portfolio.state import company_imports, company_records, firm_layer, load_company, snapshot
from vista.recorder_analysis import public_report

router = APIRouter(tags=["portfolio"])


# ---- Portfolio ---------------------------------------------------------------------


@router.get("/portfolio/me")
def me(ctx: FirmContext = Depends(firm_context)) -> dict:
    return {
        "email": ctx.principal.email,
        "name": ctx.membership.display_name or ctx.principal.email,
        "role": ctx.membership.role,
        "firm": {"id": str(ctx.firm.id), "name": ctx.firm.name, "slug": ctx.firm.slug},
        "companies": [{"id": str(c.id), "slug": c.slug, "name": c.name} for c in ctx.companies],
    }


@router.get("/portfolio")
def portfolio(records: bool = True, ctx: FirmContext = Depends(firm_context)) -> dict:
    """Full workspace snapshot (metrics, companies, tasks, opportunities, agents, activity)."""
    with platform_session() as session:
        return snapshot(session, ctx, include_records=records)


@router.get("/portfolio/companies")
def portfolio_companies(ctx: FirmContext = Depends(firm_context)) -> list[dict]:
    with platform_session() as session:
        snap = snapshot(session, ctx, include_records=False)
    return [{k: v for k, v in c.items() if k not in ("importJobs",)} for c in snap["companies"]]


@router.get("/portfolio/attention")
def portfolio_attention(ctx: FirmContext = Depends(firm_context)) -> list[dict]:
    with platform_session() as session:
        return snapshot(session, ctx, include_records=True)["attention"]


@router.get("/portfolio/activity")
def portfolio_activity(limit: int = 60, ctx: FirmContext = Depends(firm_context)) -> list[dict]:
    with platform_session() as session:
        _opps, activity = firm_layer(session, ctx, activity_limit=max(1, min(limit, 500)))
    return activity


@router.post("/portfolio/analysis", status_code=201)
def run_analysis(ctx: FirmContext = Depends(writer_context)) -> dict:
    with platform_session() as session:
        found = service.run_portfolio_analysis(session, ctx)
        return {"found": [ser.opportunity(o) for o in found], "count": len(found)}


@router.post("/portfolio/interpretation", status_code=202)
def run_interpretation(ctx: FirmContext = Depends(writer_context)) -> dict:
    """Queue the interpretation layer over canonical rows: one File Reviewer run per
    company plus one Sector Merger run per sector, executed by the job worker."""
    with platform_session() as session:
        return interpret.run_portfolio_interpretation(session, ctx)


@router.get("/portfolio/interpretation/{request_id}")
def interpretation_status(request_id: uuid.UUID, ctx: FirmContext = Depends(firm_context)) -> dict:
    with platform_session() as session:
        return interpret.interpretation_status(session, ctx, request_id)


class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    location: str = ""
    industry: str = ""
    description: str = ""
    acquired: str | None = None


@router.post("/portfolio/companies", status_code=201)
def create_company(body: CompanyCreate, ctx: FirmContext = Depends(writer_context)) -> dict:
    with platform_session() as session:
        fc = service.create_company(session, ctx, body.model_dump())
        return ser.company_profile(fc)


# ---- Company workspace -----------------------------------------------------------------


@router.get("/companies/{company_id}")
def company(company_id: str, ctx: FirmContext = Depends(firm_context)) -> dict:
    ref = ctx.company(company_id)
    c, _m = load_company(ref, include_records=False)
    for key in ("_tasks", "_agents", "_runs", "_findings"):
        c[key.lstrip("_")] = c.pop(key)
    return c


def _records(company_id: str, ctx: FirmContext, key: str) -> list[dict]:
    ref = ctx.company(company_id)
    with company_session(ref) as ts:
        return company_records(ts)[key]


@router.get("/companies/{company_id}/customers")
def customers(company_id: str, ctx: FirmContext = Depends(firm_context)) -> list[dict]:
    return _records(company_id, ctx, "customers")


@router.get("/companies/{company_id}/invoices")
def invoices(company_id: str, ctx: FirmContext = Depends(firm_context)) -> list[dict]:
    return _records(company_id, ctx, "invoices")


@router.get("/companies/{company_id}/vendors")
def vendors(company_id: str, ctx: FirmContext = Depends(firm_context)) -> list[dict]:
    return _records(company_id, ctx, "vendors")


@router.get("/companies/{company_id}/purchases")
def purchases(company_id: str, ctx: FirmContext = Depends(firm_context)) -> list[dict]:
    return _records(company_id, ctx, "purchases")


@router.get("/companies/{company_id}/subscriptions")
def subscriptions(company_id: str, ctx: FirmContext = Depends(firm_context)) -> list[dict]:
    return _records(company_id, ctx, "subscriptions")


@router.get("/companies/{company_id}/policies")
def policies(company_id: str, ctx: FirmContext = Depends(firm_context)) -> list[dict]:
    return _records(company_id, ctx, "policies")


@router.get("/companies/{company_id}/purchase-orders")
def purchase_orders(company_id: str, ctx: FirmContext = Depends(firm_context)) -> dict:
    ref = ctx.company(company_id)
    with company_session(ref) as ts:
        records = company_records(ts)
    return {"purchaseOrders": records["purchaseOrders"], "lines": records["purchaseOrderLines"]}


@router.get("/companies/{company_id}/inventory")
def inventory(company_id: str, ctx: FirmContext = Depends(firm_context)) -> list[dict]:
    return _records(company_id, ctx, "inventory")


@router.get("/companies/{company_id}/tasks")
def company_tasks(company_id: str, ctx: FirmContext = Depends(firm_context)) -> list[dict]:
    ref = ctx.company(company_id)
    c, _m = load_company(ref, include_records=False)
    return c["_tasks"]


@router.get("/companies/{company_id}/imports")
def company_import_jobs(company_id: str, ctx: FirmContext = Depends(firm_context)) -> list[dict]:
    ref = ctx.company(company_id)
    with company_session(ref) as ts:
        jobs, _open = company_imports(ts)
    return jobs


# ---- Recorder reports (employee evidence, published by the employee) --------------------


def _report_scopes(ref) -> set[tuple[str, str]]:
    """The upload workspaces that mean "this company": the firm company itself, or the
    Deal the company workspace and the recorder scope by. Re-checked per row even though
    the schema is the company's, so a report never leaks across a re-linked tenant."""
    scopes = {("company", str(ref.id))}
    if ref.deal_id:
        scopes.add(("deal", str(ref.deal_id)))
    return scopes


@router.get("/companies/{company_id}/reports")
def company_reports(company_id: str, ctx: FirmContext = Depends(firm_context)) -> list[dict]:
    """Published recording reports for this company. Drafts stay private to the employee
    until they publish; nothing here can change that."""
    ref = ctx.company(company_id)
    scopes = _report_scopes(ref)
    with company_session(ref) as ts:
        rows = ts.scalars(
            select(RecorderReport)
            .where(RecorderReport.status == "published")
            .order_by(RecorderReport.published_at.desc(), RecorderReport.id)
            .limit(200)
        ).all()
        return [public_report(r, full=False) for r in rows if (r.workspace.get("kind"), r.workspace.get("id")) in scopes]


@router.get("/companies/{company_id}/reports/{report_id}")
def company_report(company_id: str, report_id: uuid.UUID, ctx: FirmContext = Depends(firm_context)) -> dict:
    ref = ctx.company(company_id)
    with company_session(ref) as ts:
        r = ts.get(RecorderReport, report_id)
        if r is None or r.status != "published" or (r.workspace.get("kind"), r.workspace.get("id")) not in _report_scopes(ref):
            raise HTTPException(404, "Report not found")
        return public_report(r, full=True)


class ExceptionDecision(BaseModel):
    decision: str | None = Field(default=None, max_length=32)


@router.post("/companies/{company_id}/exceptions/{exception_id}/resolve")
def resolve_company_exception(
    company_id: str, exception_id: str, body: ExceptionDecision, ctx: FirmContext = Depends(writer_context)
) -> dict:
    ref = ctx.company(company_id)
    if body.decision is None:
        raise HTTPException(422, "A decision is required")
    with platform_session() as session:
        return ser.import_exception(service.resolve_exception(session, ctx, ref, exception_id, body.decision))


# ---- Imports ----------------------------------------------------------------------------


_job_view = import_service.job_view


@router.get("/import-datasets")
def import_datasets(_ctx: FirmContext = Depends(firm_context)) -> dict:
    return {
        key: {
            "label": d["label"],
            "entity": d.get("entity"),
            "fields": {f: {"label": spec["label"], "required": bool(spec.get("required"))} for f, spec in d.get("fields", {}).items()},
        }
        for key, d in DATASETS.items()
    }


class ImportUpload(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=11_000_000, repr=False)
    mime_type: str = Field(default="", max_length=128)


@router.post("/companies/{company_id}/imports", status_code=201)
def upload_import(company_id: str, body: ImportUpload, ctx: FirmContext = Depends(writer_context)) -> dict:
    ref = ctx.company(company_id)
    try:
        content = base64.b64decode(body.content, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(422, "Invalid file encoding") from exc
    filename = body.name.replace("\\", "/").rsplit("/", 1)[-1]
    if not filename:
        raise HTTPException(422, "A filename is required")
    with platform_session() as session:
        job = import_service.create_import(session, ctx, ref, filename, content, body.mime_type)
    return _job_view(ref, job.id)


@router.get("/import-jobs/{import_job_id}")
def get_import(import_job_id: uuid.UUID, ctx: FirmContext = Depends(firm_context)) -> dict:
    ref, _job = import_service.find_job(ctx, import_job_id)
    return _job_view(ref, import_job_id)


@router.get("/import-jobs/{import_job_id}/mappings")
def get_mappings(import_job_id: uuid.UUID, ctx: FirmContext = Depends(firm_context)) -> list[dict]:
    ref, _job = import_service.find_job(ctx, import_job_id)
    return _job_view(ref, import_job_id)["mappings"]


class DatasetChange(BaseModel):
    dataset: str = Field(max_length=32)


@router.post("/import-jobs/{import_job_id}/dataset")
def change_dataset(import_job_id: uuid.UUID, body: DatasetChange, ctx: FirmContext = Depends(writer_context)) -> dict:
    ref, _job = import_service.find_job(ctx, import_job_id)
    import_service.set_dataset(ctx, ref, import_job_id, body.dataset)
    return _job_view(ref, import_job_id)


@router.get("/import-jobs/{import_job_id}/preview")
def preview_import(import_job_id: uuid.UUID, ctx: FirmContext = Depends(firm_context)) -> list[dict]:
    ref, _job = import_service.find_job(ctx, import_job_id)
    return import_service.preview(ctx, ref, import_job_id)


class MappingDecision(BaseModel):
    source: str = Field(max_length=255)
    target: str | None = Field(default=None, max_length=64)
    confirmed: bool = False


class MappingApproval(BaseModel):
    mappings: list[MappingDecision] = Field(default_factory=list, max_length=200)


@router.post("/import-jobs/{import_job_id}/mappings/approve")
def approve_mappings(import_job_id: uuid.UUID, body: MappingApproval, ctx: FirmContext = Depends(writer_context)) -> dict:
    ref, _job = import_service.find_job(ctx, import_job_id)
    with platform_session() as session:
        import_service.approve_mappings(session, ctx, ref, import_job_id, [m.model_dump() for m in body.mappings])
    return _job_view(ref, import_job_id)


@router.get("/import-jobs/{import_job_id}/exceptions")
def import_exceptions(import_job_id: uuid.UUID, ctx: FirmContext = Depends(firm_context)) -> list[dict]:
    ref, _job = import_service.find_job(ctx, import_job_id)
    return _job_view(ref, import_job_id)["exceptions"]


@router.post("/import-jobs/{import_job_id}/exceptions/{exception_ref}")
def decide_exception(
    import_job_id: uuid.UUID, exception_ref: str, body: ExceptionDecision, ctx: FirmContext = Depends(writer_context)
) -> dict:
    ref, _job = import_service.find_job(ctx, import_job_id)
    with platform_session() as session:
        x = import_service.resolve_exception(session, ctx, ref, import_job_id, exception_ref, body.decision)
    return ser.import_exception(x)


@router.post("/import-jobs/{import_job_id}/approve")
def approve_import(import_job_id: uuid.UUID, ctx: FirmContext = Depends(writer_context)) -> dict:
    ref, _job = import_service.find_job(ctx, import_job_id)
    with platform_session() as session:
        import_service.approve_import(session, ctx, ref, import_job_id)
    return _job_view(ref, import_job_id)


# ---- Opportunities ------------------------------------------------------------------------


@router.get("/opportunities")
def opportunities(ctx: FirmContext = Depends(firm_context)) -> list[dict]:
    with platform_session() as session:
        opps, _activity = firm_layer(session, ctx, activity_limit=1)
    return opps


class StatusChange(BaseModel):
    status: str = Field(max_length=32)


@router.post("/opportunities/{ref}/status")
def opportunity_status(ref: str, body: StatusChange, ctx: FirmContext = Depends(writer_context)) -> dict:
    with platform_session() as session:
        return ser.opportunity(service.set_opportunity_status(session, ctx, ref, body.status))


# ---- Tasks ----------------------------------------------------------------------------------


class TaskCreate(BaseModel):
    companyId: str
    title: str = Field(min_length=1, max_length=500)
    description: str = ""
    category: str = "Integration"
    sourceType: str | None = None
    sourceId: str | None = None
    assignee: str | None = None
    priority: str = "Medium"
    dueDate: str | None = None


class TaskPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    assignee: str | None = None
    priority: str | None = None
    status: str | None = None
    dueDate: str | None = None
    outcome: str | None = None
    outcomeNotes: str | None = None
    realizedResult: float | None = None


@router.get("/tasks")
def tasks(ctx: FirmContext = Depends(firm_context)) -> list[dict]:
    with platform_session() as session:
        return snapshot(session, ctx, include_records=False)["tasks"]


@router.post("/tasks", status_code=201)
def create_task(body: TaskCreate, ctx: FirmContext = Depends(writer_context)) -> dict:
    ref = ctx.company(body.companyId)
    with platform_session() as session:
        return ser.task(service.create_task(session, ctx, ref, body.model_dump()))


@router.post("/tasks/{task_ref}")
def update_task(task_ref: str, body: TaskPatch, ctx: FirmContext = Depends(writer_context)) -> dict:
    with platform_session() as session:
        return ser.task(service.update_task(session, ctx, task_ref, body.model_dump(exclude_unset=True)))


# ---- Findings (the ledger the company workspace and the agents share) ------------------


@router.post("/findings/{finding_ref}/status")
def finding_status(finding_ref: str, body: StatusChange, ctx: FirmContext = Depends(writer_context)) -> dict:
    """Analyst triage of a ledger finding by display ref (F-012) or uuid."""
    with platform_session() as session:
        return service.set_finding_status(session, ctx, finding_ref, body.status)
