import hashlib
import json
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from vista.automation.schemas import DecisionCreate, DecisionOut, EligibilityOut, VersionCreate, VersionOut, WorkflowCreate, WorkflowOut
from vista.models.tenant import Workflow, WorkflowApproval, WorkflowVersion

EXECUTION_ROLES = {"admin", "operator"}


def definition_hash(definition: dict) -> str:
    return hashlib.sha256(json.dumps(definition, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def get_workflow(session: Session, company_id: uuid.UUID, workflow_id: uuid.UUID, *, lock: bool = False) -> Workflow:
    query = select(Workflow).where(Workflow.id == workflow_id, Workflow.company_id == company_id)
    if lock:
        query = query.with_for_update()
    workflow = session.scalar(query)
    if workflow is None:
        raise HTTPException(404, "Workflow not found")
    return workflow


def get_version(session: Session, workflow: Workflow, version_id: uuid.UUID) -> WorkflowVersion:
    version = session.scalar(select(WorkflowVersion).where(WorkflowVersion.id == version_id, WorkflowVersion.workflow_id == workflow.id))
    if version is None:
        raise HTTPException(404, "Workflow version not found")
    return version


def decision_for(session: Session, version: WorkflowVersion) -> WorkflowApproval | None:
    return session.scalar(select(WorkflowApproval).where(WorkflowApproval.version_id == version.id))


def version_out(version: WorkflowVersion, decision: WorkflowApproval | None) -> VersionOut:
    return VersionOut(
        id=version.id,
        workflow_id=version.workflow_id,
        number=version.number,
        definition=version.definition,
        definition_hash=version.definition_hash,
        status=decision.decision if decision else "draft",
        decision=DecisionOut.model_validate(decision) if decision else None,
        created_by=version.created_by,
        created_at=version.created_at,
    )


def workflow_out(session: Session, workflow: Workflow) -> WorkflowOut:
    version = session.scalars(
        select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow.id, WorkflowVersion.number == workflow.latest_version)
    ).one()
    return WorkflowOut(
        id=workflow.id,
        company_id=workflow.company_id,
        name=workflow.name,
        latest_version=version_out(version, decision_for(session, version)),
        created_by=workflow.created_by,
        created_at=workflow.created_at,
    )


def _add_version(session: Session, workflow: Workflow, definition: dict, user_id: uuid.UUID) -> WorkflowVersion:
    version = WorkflowVersion(
        workflow_id=workflow.id,
        number=workflow.latest_version,
        definition=definition,
        definition_hash=definition_hash(definition),
        created_by=user_id,
    )
    session.add(version)
    session.flush()
    return version


def create_workflow(session: Session, company_id: uuid.UUID, user_id: uuid.UUID, body: WorkflowCreate) -> WorkflowOut:
    workflow = Workflow(company_id=company_id, name=body.name, created_by=user_id)
    session.add(workflow)
    session.flush()
    _add_version(session, workflow, body.definition.model_dump(mode="json"), user_id)
    result = workflow_out(session, workflow)
    session.commit()
    return result


def create_version(session: Session, workflow: Workflow, user_id: uuid.UUID, body: VersionCreate) -> VersionOut:
    if body.expected_version != workflow.latest_version:
        raise HTTPException(409, "The workflow has a newer version; reload before editing")
    workflow.latest_version += 1
    version = _add_version(session, workflow, body.definition.model_dump(mode="json"), user_id)
    result = version_out(version, None)
    session.commit()
    return result


def record_decision(session: Session, workflow: Workflow, version: WorkflowVersion, user_id: uuid.UUID, body: DecisionCreate) -> VersionOut:
    if version.number != workflow.latest_version:
        raise HTTPException(409, "Only the latest version can receive a decision")
    decision = decision_for(session, version)
    if decision is not None:
        if decision.decision != body.decision or decision.reason != body.reason:
            raise HTTPException(409, "This version already has a final decision; create a new version")
    else:
        decision = WorkflowApproval(
            version_id=version.id,
            definition_hash=version.definition_hash,
            decision=body.decision,
            reason=body.reason,
            decided_by=user_id,
        )
        session.add(decision)
        session.flush()
    result = version_out(version, decision)
    session.commit()
    return result


def execution_eligibility(session: Session, workflow: Workflow, version: WorkflowVersion, role: str) -> EligibilityOut:
    decision = decision_for(session, version)
    reasons = []
    if role not in EXECUTION_ROLES:
        reasons.append("execution_permission_required")
    if version.number != workflow.latest_version:
        reasons.append("version_superseded")
    if decision is None or decision.decision != "approved":
        reasons.append("version_not_approved")
    if version.definition.get("environment") != "sandbox":
        reasons.append("unsupported_environment")
    digest = definition_hash(version.definition)
    if digest != version.definition_hash or (decision is not None and decision.definition_hash != digest):
        reasons.append("definition_hash_mismatch")
    return EligibilityOut(version_id=version.id, eligible=not reasons, reasons=reasons)
