import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from vista.automation import graph_review, service
from vista.automation.schemas import DecisionCreate, EligibilityOut, VersionCreate, VersionOut, WorkflowCreate, WorkflowOut
from vista.models.tenant import Workflow, WorkflowApproval, WorkflowVersion
from vista.portfolio.access import FirmContext, company_session, firm_context, writer_context

router = APIRouter(prefix="/companies/{company_id}/workflows", tags=["workflows"])


@router.post("", response_model=WorkflowOut, status_code=201)
def create_workflow(company_id: str, body: WorkflowCreate, ctx: FirmContext = Depends(writer_context)) -> WorkflowOut:
    company = ctx.company(company_id)
    with company_session(company) as session:
        return service.create_workflow(session, company.id, ctx.principal.user_id, body)


@router.get("", response_model=list[WorkflowOut])
def list_workflows(
    company_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: FirmContext = Depends(firm_context),
) -> list[WorkflowOut]:
    company = ctx.company(company_id)
    with company_session(company) as session:
        workflows = session.scalars(
            select(Workflow)
            .where(Workflow.company_id == company.id)
            .order_by(Workflow.created_at.desc(), Workflow.id)
            .offset(offset)
            .limit(limit)
        ).all()
        return [service.workflow_out(session, workflow) for workflow in workflows]


@router.get("/{workflow_id}", response_model=WorkflowOut)
def get_workflow(company_id: str, workflow_id: uuid.UUID, ctx: FirmContext = Depends(firm_context)) -> WorkflowOut:
    company = ctx.company(company_id)
    with company_session(company) as session:
        return service.workflow_out(session, service.get_workflow(session, company.id, workflow_id))


@router.post("/{workflow_id}/versions", response_model=VersionOut, status_code=201)
def create_version(company_id: str, workflow_id: uuid.UUID, body: VersionCreate, ctx: FirmContext = Depends(writer_context)) -> VersionOut:
    company = ctx.company(company_id)
    with company_session(company) as session:
        workflow = service.get_workflow(session, company.id, workflow_id, lock=True)
        return service.create_version(session, workflow, ctx.principal.user_id, body)


@router.get("/{workflow_id}/versions", response_model=list[VersionOut])
def list_versions(
    company_id: str,
    workflow_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: FirmContext = Depends(firm_context),
) -> list[VersionOut]:
    company = ctx.company(company_id)
    with company_session(company) as session:
        workflow = service.get_workflow(session, company.id, workflow_id)
        rows = session.execute(
            select(WorkflowVersion, WorkflowApproval)
            .outerjoin(WorkflowApproval, WorkflowApproval.version_id == WorkflowVersion.id)
            .where(WorkflowVersion.workflow_id == workflow.id)
            .order_by(WorkflowVersion.number.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return [service.version_out(version, decision) for version, decision in rows]


@router.get("/{workflow_id}/versions/{version_id}", response_model=VersionOut)
def get_version(company_id: str, workflow_id: uuid.UUID, version_id: uuid.UUID, ctx: FirmContext = Depends(firm_context)) -> VersionOut:
    company = ctx.company(company_id)
    with company_session(company) as session:
        workflow = service.get_workflow(session, company.id, workflow_id)
        version = service.get_version(session, workflow, version_id)
        return service.version_out(version, service.decision_for(session, version))


@router.post("/{workflow_id}/versions/{version_id}/decision", response_model=VersionOut)
def decide_version(
    company_id: str,
    workflow_id: uuid.UUID,
    version_id: uuid.UUID,
    body: DecisionCreate,
    ctx: FirmContext = Depends(firm_context),
) -> VersionOut:
    company = ctx.company(company_id)
    if ctx.membership.role != "admin":
        raise HTTPException(403, "Firm admin role required to approve or reject workflows")
    with company_session(company) as session:
        workflow = service.get_workflow(session, company.id, workflow_id, lock=True)
        version = service.get_version(session, workflow, version_id)
        return service.record_decision(session, workflow, version, ctx.principal.user_id, body)


@router.get("/{workflow_id}/versions/{version_id}/graph", response_model=graph_review.GraphReviewOut)
def version_graph(
    company_id: str, workflow_id: uuid.UUID, version_id: uuid.UUID, ctx: FirmContext = Depends(firm_context)
) -> graph_review.GraphReviewOut:
    company = ctx.company(company_id)
    with company_session(company) as session:
        workflow = service.get_workflow(session, company.id, workflow_id)
        version = service.get_version(session, workflow, version_id)
        return graph_review.review(session, workflow, version)


@router.post("/{workflow_id}/versions/{version_id}/graph/draft", response_model=VersionOut, status_code=201)
def draft_from_runs(
    company_id: str,
    workflow_id: uuid.UUID,
    version_id: uuid.UUID,
    body: graph_review.GraphDraftCreate,
    ctx: FirmContext = Depends(firm_context),
) -> VersionOut:
    company = ctx.company(company_id)
    if ctx.membership.role != "admin":
        raise HTTPException(403, "Firm admin role required to draft from runs")
    with company_session(company) as session:
        workflow = service.get_workflow(session, company.id, workflow_id, lock=True)
        version = service.get_version(session, workflow, version_id)
        return graph_review.create_draft(session, workflow, version, ctx.principal.user_id, body)


@router.get("/{workflow_id}/versions/{version_id}/eligibility", response_model=EligibilityOut)
def version_eligibility(
    company_id: str, workflow_id: uuid.UUID, version_id: uuid.UUID, ctx: FirmContext = Depends(firm_context)
) -> EligibilityOut:
    company = ctx.company(company_id)
    with company_session(company) as session:
        workflow = service.get_workflow(session, company.id, workflow_id, lock=True)
        version = service.get_version(session, workflow, version_id)
        from vista.computer_use import tools

        availability = tools.availability(session, company.id, version.definition.get("allowed_tools", []))
        return service.execution_eligibility(session, workflow, version, ctx.membership.role, availability=availability)
