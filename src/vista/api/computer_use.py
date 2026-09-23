"""Computer Use Agent routes.

Tenant workspace (cookie or personal key): start a run of an approved version, watch it,
decide a paused step, stop it. Only a tenant `admin` may start, decide or stop — the tenant
analogue of the firm `admin` that approves versions.

Recorder (personal key): presence + offers, claim with consent, poll (= heartbeat + next
step), post a step result, close. The recorder never plans; it only performs what a step
request says and reports what it saw.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from vista.api.company_workflows import _company_id
from vista.auth import Principal, current_principal
from vista.automation import service as automation
from vista.computer_use import service
from vista.computer_use.schemas import ClaimIn, DecisionIn, RunStart, SessionStopIn, StepResultIn, StopIn, WorkflowRunOut
from vista.config import settings
from vista.db import tenant_session
from vista.models.tenant import HarnessSession, HarnessStep, WorkflowRun

router = APIRouter(tags=["workflow runs"])
recorder_router = APIRouter(prefix="/recorder/computer-use", tags=["recorder"])

EXECUTION_ROLES_TENANT = {"admin"}


def _executor(principal: Principal = Depends(current_principal)) -> Principal:
    if principal.role not in EXECUTION_ROLES_TENANT:
        raise HTTPException(403, "Workspace admin role required to run, decide or stop workflows")
    return principal


# ---- tenant workspace ---------------------------------------------------------------------------


@router.post("/workflows/{workflow_id}/versions/{version_id}/runs", response_model=WorkflowRunOut, status_code=201)
def start_run(workflow_id: uuid.UUID, version_id: uuid.UUID, body: RunStart, principal: Principal = Depends(_executor)) -> WorkflowRunOut:
    company_id = _company_id(principal)
    with tenant_session(principal.tenant_schema) as session:
        workflow = automation.get_workflow(session, company_id, workflow_id, lock=True)
        version = automation.get_version(session, workflow, version_id)
        run = service.start_run(
            session, principal=principal, company_id=company_id, workflow=workflow, version=version, body=body, role=principal.role
        )
        return service.run_out(session, run)


@router.get("/workflows/{workflow_id}/runs", response_model=list[WorkflowRunOut])
def list_runs(
    workflow_id: uuid.UUID, limit: int = Query(50, ge=1, le=100), principal: Principal = Depends(current_principal)
) -> list[WorkflowRunOut]:
    company_id = _company_id(principal)
    with tenant_session(principal.tenant_schema) as session:
        workflow = automation.get_workflow(session, company_id, workflow_id)
        return [service.run_out(session, r) for r in service.list_runs(session, workflow, limit=limit)]


@router.get("/workflow-runs/{run_id}", response_model=WorkflowRunOut)
def get_run(run_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> WorkflowRunOut:
    company_id = _company_id(principal)
    with tenant_session(principal.tenant_schema) as session:
        return service.run_out(session, service.get_run(session, company_id, run_id))


@router.post("/workflow-runs/{run_id}/decision", response_model=WorkflowRunOut)
def decide_step(run_id: uuid.UUID, body: DecisionIn, principal: Principal = Depends(_executor)) -> WorkflowRunOut:
    company_id = _company_id(principal)
    with tenant_session(principal.tenant_schema) as session:
        run = service.get_run(session, company_id, run_id, lock=True)
        return service.run_out(session, service.decide(session, run, principal, body))


@router.post("/workflow-runs/{run_id}/stop", response_model=WorkflowRunOut)
def stop_run(run_id: uuid.UUID, body: StopIn, principal: Principal = Depends(_executor)) -> WorkflowRunOut:
    company_id = _company_id(principal)
    with tenant_session(principal.tenant_schema) as session:
        run = service.get_run(session, company_id, run_id, lock=True)
        return service.run_out(session, service.stop(session, run, principal, body.reason))


@router.get("/workflow-runs/{run_id}/steps/{step_id}/screenshot")
def step_screenshot(run_id: uuid.UUID, step_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> Response:
    """The evidence screenshot the recorder shared for one step, if the employee consented."""
    company_id = _company_id(principal)
    with tenant_session(principal.tenant_schema) as session:
        run = service.get_run(session, company_id, run_id)
        step = session.get(HarnessStep, step_id)
        if step is None or step.workflow_run_id != run.id:
            raise HTTPException(404, "Step not found")
        evidence = (step.result or {}).get("evidence") or {}
        key = evidence.get("artifact_key")
        if not key:
            raise HTTPException(404, "No screenshot was shared for this step")
    from vista.storage import s3_client

    obj = s3_client().get_object(Bucket=settings.s3_bucket, Key=key)
    with obj["Body"] as stream:
        data = stream.read(2 * 1024 * 1024 + 1)
    return Response(
        content=data, media_type=str(evidence.get("content_type") or "image/png"), headers={"Cache-Control": "private, max-age=60"}
    )


# ---- recorder ----------------------------------------------------------------------------------------


def _session_for(session, principal: Principal, session_id: uuid.UUID) -> tuple[HarnessSession, WorkflowRun]:
    hs = session.get(HarnessSession, session_id)
    if hs is None or hs.user_id != principal.user_id:
        raise HTTPException(404, "Session not found")
    run = session.get(WorkflowRun, hs.workflow_run_id)
    if run is None:
        raise HTTPException(404, "Session not found")
    return hs, run


@recorder_router.get("/sessions")
def list_sessions(
    device_id: str = Query(min_length=1, max_length=128),
    platform: str = Query("", max_length=32),
    browser: bool = Query(False),
    desktop: bool = Query(False),
    recorder_version: str = Query("", max_length=32),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Presence + offers. Called on the recorder's idle tick; returns within one request
    (no long-poll) so the Cloudflare Worker's timeout never bites."""
    company_id = _company_id(principal)
    with tenant_session(principal.tenant_schema) as session:
        device = service.presence(
            session,
            principal,
            company_id,
            device_id,
            platform,
            {"browser": browser, "desktop": desktop, "recorder_version": recorder_version},
        )
        active = service.active_session(session, principal, device_id)
        active_view = None
        if active is not None:
            run = session.get(WorkflowRun, active.workflow_run_id)
            if run is not None and run.status not in service.TERMINAL:
                active_view = service.session_view(session, active, run, claim_step=False)
            else:
                active.status = "closed"
        offers = [service.offer_view(session, r) for r in service.offers(session, principal, company_id)]
        session.commit()
        return {
            "device": {"id": str(device.id), "device_id": device.device_id, "capabilities": device.capabilities},
            "poll_seconds": 30,
            "active": active_view,
            "offers": offers,
        }


@recorder_router.post("/sessions/{run_id}/claim")
def claim_session(run_id: uuid.UUID, body: ClaimIn, principal: Principal = Depends(current_principal)) -> dict:
    company_id = _company_id(principal)
    with tenant_session(principal.tenant_schema) as session:
        run = service.get_run(session, company_id, run_id, lock=True)
        hs, token = service.claim(session, run, principal, body)
        view = service.session_view(session, hs, run, claim_step=False)
        view["lease"]["token"] = token
        return view


@recorder_router.get("/sessions/{session_id}")
def poll_session(
    session_id: uuid.UUID,
    device_id: str = Query(min_length=1, max_length=128),
    lease_token: str = Query(min_length=1, max_length=64),
    principal: Principal = Depends(current_principal),
) -> dict:
    with tenant_session(principal.tenant_schema) as session:
        hs, run = _session_for(session, principal, session_id)
        return service.poll(session, hs, run, device_id=device_id, token=lease_token)


@recorder_router.post("/steps/{step_id}/result")
def post_step_result(step_id: uuid.UUID, body: StepResultIn, principal: Principal = Depends(current_principal)) -> dict:
    with tenant_session(principal.tenant_schema) as session:
        step = session.get(HarnessStep, step_id)
        if step is None or step.harness_session_id is None:
            raise HTTPException(404, "Step not found")
        hs, run = _session_for(session, principal, step.harness_session_id)
        return service.post_result(session, hs, run, step, principal, body)


@recorder_router.post("/sessions/{session_id}/stop")
def stop_session(session_id: uuid.UUID, body: SessionStopIn, principal: Principal = Depends(current_principal)) -> dict:
    with tenant_session(principal.tenant_schema) as session:
        hs, run = _session_for(session, principal, session_id)
        return service.close_session(session, hs, run, principal, body)
