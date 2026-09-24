import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text

from vista.auth import Principal, current_principal
from vista.db import tenant_session
from vista.models.tenant import RecorderSubmission
from vista.recorder_analysis import public_report
from vista.recorder_uploads import (
    AnswersIn,
    PublishIn,
    SubmissionCreate,
    accept_submission,
    manifest_hash,
    public_submission,
    publish_report,
    published_reports,
    queue_analysis,
    readable_report,
    record_answers,
    report_for,
    resolve_workspace,
    signed_uploads,
    submission_for,
    workspace_keys,
    workspaces_for,
)

router = APIRouter(prefix="/recorder", tags=["recorder uploads"])


@router.get("/workspaces")
def workspaces(principal: Principal = Depends(current_principal)) -> dict:
    return {
        "user_id": str(principal.user_id),
        "tenant_id": str(principal.tenant_id),
        "email": principal.email,
        "workspaces": workspaces_for(principal),
    }


@router.post("/submissions", status_code=201)
def create_submission(body: SubmissionCreate, principal: Principal = Depends(current_principal)) -> dict:
    workspace = resolve_workspace(principal, body.workspace)
    manifest = body.model_dump(mode="json")
    digest = manifest_hash(manifest)
    with tenant_session(principal.tenant_schema) as session:
        lock_key = f"recorder:{principal.tenant_id}:{principal.user_id}:{body.device_id}:{body.source_id}"
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": lock_key})
        row = session.scalar(
            select(RecorderSubmission).where(
                RecorderSubmission.uploaded_by == principal.user_id,
                RecorderSubmission.device_id == body.device_id,
                RecorderSubmission.source_id == body.source_id,
            )
        )
        if row is not None and row.manifest_hash != digest:
            raise HTTPException(409, "This session already has a different immutable upload package")
        if row is None:
            row = RecorderSubmission(
                uploaded_by=principal.user_id,
                device_id=body.device_id,
                source_id=body.source_id,
                canonical_company_id=uuid.UUID(workspace["canonical_company_id"]) if workspace["canonical_company_id"] else None,
                manifest=manifest,
                manifest_hash=digest,
            )
            session.add(row)
            session.flush()
        result = public_submission(row)
        session.commit()
        return result


@router.get("/submissions")
def list_submissions(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(current_principal),
) -> list[dict]:
    allowed = workspace_keys(principal)
    with tenant_session(principal.tenant_schema) as session:
        rows = session.scalars(
            select(RecorderSubmission)
            .where(RecorderSubmission.uploaded_by == principal.user_id)
            .order_by(RecorderSubmission.created_at.desc(), RecorderSubmission.id)
            .offset(offset)
            .limit(limit)
        ).all()
        return [
            public_submission(row, report_for(session, row))
            for row in rows
            if (row.manifest["workspace"]["kind"], row.manifest["workspace"]["id"]) in allowed
        ]


@router.get("/submissions/{submission_id}")
def get_submission(submission_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> dict:
    with tenant_session(principal.tenant_schema) as session:
        row = submission_for(session, principal, submission_id)
        return public_submission(row, report_for(session, row), full_report=True)


@router.post("/submissions/{submission_id}/upload-urls")
def upload_urls(submission_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> dict:
    with tenant_session(principal.tenant_schema) as session:
        row = submission_for(session, principal, submission_id, lock=True)
        return {"submission": public_submission(row), "uploads": signed_uploads(principal, row)}


@router.post("/submissions/{submission_id}/complete")
def complete_submission(submission_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> dict:
    with tenant_session(principal.tenant_schema) as session:
        return accept_submission(session, principal, submission_for(session, principal, submission_id, lock=True))


@router.post("/submissions/{submission_id}/analyze")
def analyze_submission(submission_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> dict:
    """Re-run the analysis of an accepted upload (after a failure, or to refresh an
    unpublished draft). No-op while an attempt is in flight or once published."""
    with tenant_session(principal.tenant_schema) as session:
        row = submission_for(session, principal, submission_id, lock=True)
        if row.upload_status != "accepted":
            raise HTTPException(409, "Finish the upload before requesting analysis")
        queue_analysis(session, principal, row)
        return public_submission(row, report_for(session, row), full_report=True)


@router.post("/submissions/{submission_id}/answers")
def answer_questions(submission_id: uuid.UUID, body: AnswersIn, principal: Principal = Depends(current_principal)) -> dict:
    with tenant_session(principal.tenant_schema) as session:
        return record_answers(session, principal, submission_id, body)


@router.post("/submissions/{submission_id}/publish")
def publish_submission(submission_id: uuid.UUID, body: PublishIn, principal: Principal = Depends(current_principal)) -> dict:
    with tenant_session(principal.tenant_schema) as session:
        return publish_report(session, principal, submission_id, body)


@router.get("/reports")
def list_reports(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(current_principal),
) -> list[dict]:
    """Published recording reports for the workspaces this user can read."""
    with tenant_session(principal.tenant_schema) as session:
        return published_reports(session, principal, limit=limit, offset=offset)


@router.get("/reports/{report_id}")
def get_report(report_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> dict:
    with tenant_session(principal.tenant_schema) as session:
        return public_report(readable_report(session, principal, report_id), full=True)
