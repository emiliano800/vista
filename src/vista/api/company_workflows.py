"""The company workspace's own view of its workflows (tenant-scoped).

`api/workflows.py` serves PE analysts through a `FirmContext` (`/companies/{id}/workflows`).
The people who act on a recorder finding — the company's operators in the tenant
workspace — sign in with tenant roles and are usually not firm members, so these routes
expose the same tables and the same `automation.service` under the tenant principal:

  viewer / member / admin   read workflows, versions, eligibility
  member / admin            create a workflow or a new version (drafts only)
  admin                     approve or reject a version, and run one — the tenant analogue of the firm admin

Tenant `users.role` is `admin|member|viewer` (see `tenancy.provision_tenant`); `owner` is a
deal-membership role and never appears on a principal.

Every version still starts as a draft, approval is bound to the definition hash, and
execution stays unavailable, exactly as through the firm API.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from vista.auth import Principal, current_principal
from vista.automation import graph_review, service
from vista.automation.schemas import DecisionCreate, EligibilityOut, VersionCreate, VersionOut, WorkflowCreate, WorkflowOut
from vista.db import platform_session, tenant_session
from vista.models.platform import FirmCompany
from vista.models.tenant import Workflow

router = APIRouter(prefix="/workflows", tags=["workflows"])

WRITE_ROLES = {"admin", "member"}
DECISION_ROLES = {"admin"}


def _company_id(principal: Principal) -> uuid.UUID:
    """The platform company this tenant is; workflows are keyed by it in both APIs."""
    with platform_session() as session:
        company_id = session.scalar(select(FirmCompany.id).where(FirmCompany.tenant_id == principal.tenant_id))
    if company_id is None:
        raise HTTPException(404, "This workspace is not linked to a company")
    return company_id


def _writer(principal: Principal = Depends(current_principal)) -> Principal:
    if principal.role not in WRITE_ROLES:
        raise HTTPException(403, "Viewers cannot create workflows")
    return principal


def _decider(principal: Principal = Depends(current_principal)) -> Principal:
    if principal.role not in DECISION_ROLES:
        raise HTTPException(403, "Workspace admin role required to approve or reject workflows")
    return principal


@router.get("", response_model=list[WorkflowOut])
def list_workflows(
    limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), principal: Principal = Depends(current_principal)
) -> list[WorkflowOut]:
    company_id = _company_id(principal)
    with tenant_session(principal.tenant_schema) as session:
        rows = session.scalars(
            select(Workflow)
            .where(Workflow.company_id == company_id)
            .order_by(Workflow.created_at.desc(), Workflow.id)
            .offset(offset)
            .limit(limit)
        ).all()
        return [service.workflow_out(session, w) for w in rows]


@router.post("", response_model=WorkflowOut, status_code=201)
def create_workflow(body: WorkflowCreate, principal: Principal = Depends(_writer)) -> WorkflowOut:
    company_id = _company_id(principal)
    with tenant_session(principal.tenant_schema) as session:
        return service.create_workflow(session, company_id, principal.user_id, body)


@router.get("/{workflow_id}", response_model=WorkflowOut)
def get_workflow(workflow_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> WorkflowOut:
    company_id = _company_id(principal)
    with tenant_session(principal.tenant_schema) as session:
        return service.workflow_out(session, service.get_workflow(session, company_id, workflow_id))


@router.get("/{workflow_id}/versions", response_model=list[VersionOut])
def list_versions(workflow_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> list[VersionOut]:
    company_id = _company_id(principal)
    with tenant_session(principal.tenant_schema) as session:
        workflow = service.get_workflow(session, company_id, workflow_id)
        versions = session.scalars(
            select(service.WorkflowVersion)
            .where(service.WorkflowVersion.workflow_id == workflow.id)
            .order_by(service.WorkflowVersion.number)
        ).all()
        return [service.version_out(v, service.decision_for(session, v)) for v in versions]


@router.post("/{workflow_id}/versions", response_model=VersionOut, status_code=201)
def create_version(workflow_id: uuid.UUID, body: VersionCreate, principal: Principal = Depends(_writer)) -> VersionOut:
    company_id = _company_id(principal)
    with tenant_session(principal.tenant_schema) as session:
        workflow = service.get_workflow(session, company_id, workflow_id, lock=True)
        return service.create_version(session, workflow, principal.user_id, body)


@router.post("/{workflow_id}/versions/{version_id}/decision", response_model=VersionOut)
def decide_version(
    workflow_id: uuid.UUID, version_id: uuid.UUID, body: DecisionCreate, principal: Principal = Depends(_decider)
) -> VersionOut:
    company_id = _company_id(principal)
    with tenant_session(principal.tenant_schema) as session:
        workflow = service.get_workflow(session, company_id, workflow_id, lock=True)
        version = service.get_version(session, workflow, version_id)
        return service.record_decision(session, workflow, version, principal.user_id, body)


@router.get("/{workflow_id}/versions/{version_id}/graph", response_model=graph_review.GraphReviewOut)
def version_graph(
    workflow_id: uuid.UUID, version_id: uuid.UUID, principal: Principal = Depends(current_principal)
) -> graph_review.GraphReviewOut:
    """The version's task graph as approved, every terminal run's path over it, and the draft the
    runs' statistics would make — derived, never stored until an admin asks for it below."""
    company_id = _company_id(principal)
    with tenant_session(principal.tenant_schema) as session:
        workflow = service.get_workflow(session, company_id, workflow_id)
        version = service.get_version(session, workflow, version_id)
        return graph_review.review(session, workflow, version)


@router.post("/{workflow_id}/versions/{version_id}/graph/draft", response_model=VersionOut, status_code=201)
def draft_from_runs(
    workflow_id: uuid.UUID, version_id: uuid.UUID, body: graph_review.GraphDraftCreate, principal: Principal = Depends(_decider)
) -> VersionOut:
    company_id = _company_id(principal)
    with tenant_session(principal.tenant_schema) as session:
        workflow = service.get_workflow(session, company_id, workflow_id, lock=True)
        version = service.get_version(session, workflow, version_id)
        return graph_review.create_draft(session, workflow, version, principal.user_id, body)


@router.get("/{workflow_id}/versions/{version_id}/eligibility", response_model=EligibilityOut)
def version_eligibility(workflow_id: uuid.UUID, version_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> EligibilityOut:
    company_id = _company_id(principal)
    with tenant_session(principal.tenant_schema) as session:
        workflow = service.get_workflow(session, company_id, workflow_id)
        version = service.get_version(session, workflow, version_id)
        from vista.computer_use import tools

        # Tenant `admin` is the execution role here (the firm API keeps its own); availability says
        # whether a harness is actually connected for this company right now.
        role = "admin" if principal.role == "admin" else principal.role
        availability = tools.availability(session, company_id, version.definition.get("allowed_tools", []))
        return service.execution_eligibility(session, workflow, version, role, availability=availability)
