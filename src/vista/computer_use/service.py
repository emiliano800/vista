"""Run lifecycle (start, decide, stop) and the recorder's side of the protocol
(presence, offers, claim, poll, result, close). Everything here is request-scoped and
never plans a step: the planner only ever runs inside the worker job, and every resume
is a new job with an idempotency key that names the step it answers.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from vista.agents.keys import agent_key_for
from vista.automation import service as automation
from vista.automation.schemas import WorkflowLimits
from vista.computer_use import tools
from vista.computer_use.schemas import ClaimIn, DecisionIn, RunStart, SessionStopIn, StepResultIn, WorkflowRunOut
from vista.config import settings
from vista.db import platform_session
from vista.jobs.queue import enqueue, find_job
from vista.models.platform import FirmCompany, User
from vista.models.tenant import (
    AgentRun,
    HarnessDevice,
    HarnessSession,
    HarnessStep,
    RecorderReport,
    RecorderSubmission,
    Workflow,
    WorkflowRun,
    WorkflowVersion,
)

RUN_TYPE = "workflow_execution"
JOB_KIND = "execute_workflow"
TERMINAL = frozenset({"succeeded", "failed", "stopped"})
CONSENT_VERSION = "computer-use-v1"


def now() -> datetime:
    return datetime.now(UTC)


def company_name(company_id: uuid.UUID) -> str:
    with platform_session() as platform:
        return platform.scalar(select(FirmCompany.name).where(FirmCompany.id == company_id)) or ""


def get_run(session: Session, company_id: uuid.UUID, run_id: uuid.UUID, *, lock: bool = False) -> WorkflowRun:
    query = select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.company_id == company_id)
    if lock:
        query = query.with_for_update()
    run = session.scalar(query)
    if run is None:
        raise HTTPException(404, "Workflow run not found")
    return run


def run_out(session: Session, run: WorkflowRun) -> WorkflowRunOut:
    workflow = session.get(Workflow, run.workflow_id)
    version = session.get(WorkflowVersion, run.workflow_version_id)
    harness = None
    if run.harness_session_id is not None:
        hs = session.get(HarnessSession, run.harness_session_id)
        if hs is not None:
            harness = {
                "device_id": hs.device_id,
                "user_id": str(hs.user_id),
                "kinds": hs.kinds,
                "connected": hs.status == "active" and hs.lease_until is not None and hs.lease_until > now(),
                "screenshots": bool((hs.consent or {}).get("screenshots")),
            }
    pending = run.pending
    if pending and pending.get("kind") == "offer":
        pending = {**pending, "harness": harness}
    return WorkflowRunOut(
        id=run.id,
        workflow_id=run.workflow_id,
        workflow_name=workflow.name if workflow else "",
        version_id=run.workflow_version_id,
        version_number=version.number if version else 0,
        definition_hash=run.definition_hash,
        agent_run_id=run.agent_run_id,
        mode=run.mode,
        status=run.status,
        steps_used=run.steps_used,
        limits=WorkflowLimits.model_validate(run.limits or {}),
        cost_usd=run.cost_usd or 0,
        pending=pending,
        harness=harness,
        outcome=run.outcome,
        error=run.error,
        requested_by=run.requested_by,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _enqueue(session: Session, run: WorkflowRun, tenant_id: uuid.UUID, key: str, *, run_at: datetime | None = None) -> None:
    """The A2A handshake: the tenant rows exist first, then the platform job, then the job id
    is written back. Idempotent on `key`, so a duplicate result or decision enqueues nothing."""
    with platform_session() as platform:
        job = enqueue(
            platform, tenant_id=tenant_id, kind=JOB_KIND, payload={"workflow_run_id": str(run.id)}, idempotency_key=key, run_at=run_at
        )
        platform.commit()
        job_id = job.id
    agent_run = session.get(AgentRun, run.agent_run_id)
    if agent_run is not None:
        agent_run.job_id = job_id
    session.commit()


# ---- start ---------------------------------------------------------------------------------------


def bind_inputs(session: Session, principal, body: RunStart) -> dict[str, dict]:
    """Only things the requester may legitimately hand the agent: a document from an accepted
    submission that is published (or their own), canonical records by id, or a literal value."""
    bound: dict[str, dict] = {}
    for name, binding in body.inputs.items():
        if binding.kind == "document":
            row = session.get(RecorderSubmission, binding.submission_id)
            if row is None or row.upload_status != "accepted":
                raise HTTPException(422, f"Input {name!r}: the submission is not an accepted upload")
            artifact = next((a for a in row.manifest["artifacts"] if a["id"] == binding.artifact_id), None)
            if artifact is None:
                raise HTTPException(422, f"Input {name!r}: artifact {binding.artifact_id} is not in that submission")
            report = session.scalar(select(RecorderReport).where(RecorderReport.submission_id == row.id))
            published = report is not None and report.status == "published"
            if not published and row.uploaded_by != principal.user_id:
                raise HTTPException(403, f"Input {name!r}: only a published document, or your own upload, can be bound")
            bound[name] = {
                "kind": "document",
                "submission_id": str(row.id),
                "artifact_id": artifact["id"],
                "filename": artifact["filename"],
            }
        elif binding.kind == "records":
            bound[name] = {"kind": "records", "table": binding.table, "ids": [str(i) for i in binding.ids]}
        else:
            bound[name] = {"kind": "value", "value": binding.value}
    return bound


def start_run(
    session: Session, *, principal, company_id: uuid.UUID, workflow: Workflow, version: WorkflowVersion, body: RunStart, role: str
) -> WorkflowRun:
    if not settings.computer_use_enabled:
        raise HTTPException(409, "Computer use is disabled on this deployment")
    definition = version.definition
    availability = tools.availability(session, company_id, definition["allowed_tools"])
    eligibility = automation.execution_eligibility(session, workflow, version, role, availability=availability)
    if not eligibility.eligible or not eligibility.execution_available:
        raise HTTPException(409, {"reasons": eligibility.reasons, "availability": availability.to_json()})
    inputs = bind_inputs(session, principal, body)
    remote_kinds = list(availability.remote_kinds)
    needs_remote = bool(remote_kinds)
    # A client key is scoped to the caller; a repeated key returns the run it already started.
    key = f"user:{principal.user_id}:{body.idempotency_key}" if body.idempotency_key else None
    if key is not None and not needs_remote:
        with platform_session() as platform:
            earlier = find_job(platform, principal.tenant_id, JOB_KIND, key)
        if earlier is not None:
            existing = session.get(WorkflowRun, uuid.UUID(earlier.payload["workflow_run_id"]))
            if existing is not None:
                return existing
    agent_run = AgentRun(
        job_id=uuid.uuid4(),  # replaced once the job row exists
        run_type=RUN_TYPE,
        agent_key=agent_key_for(RUN_TYPE),
        company=company_name(company_id)[:64] or None,
        requested_by=principal.user_id,
        status="waiting" if needs_remote else "queued",
    )
    session.add(agent_run)
    session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        definition_hash=version.definition_hash,
        agent_run_id=agent_run.id,
        company_id=company_id,
        requested_by=principal.user_id,
        mode=body.mode,
        status="waiting_for_harness" if needs_remote else "queued",
        inputs=inputs,
        limits=dict(definition.get("limits") or WorkflowLimits().model_dump(mode="json")),
        checkpoint={},
    )
    if needs_remote:
        run.pending = {
            "kind": "offer",
            "harness_kinds": remote_kinds,
            "expires_at": (now() + timedelta(seconds=settings.computer_use_offer_ttl_s)).isoformat(),
            "device": availability.device,
        }
    session.add(run)
    session.commit()
    if not needs_remote:
        _enqueue(session, run, principal.tenant_id, key or f"workflow_run:{run.id}:start")
    return run


def list_runs(session: Session, workflow: Workflow, *, limit: int = 50) -> list[WorkflowRun]:
    return list(
        session.scalars(
            select(WorkflowRun).where(WorkflowRun.workflow_id == workflow.id).order_by(WorkflowRun.created_at.desc()).limit(limit)
        ).all()
    )


# ---- owner decisions ------------------------------------------------------------------------------


def decide(session: Session, run: WorkflowRun, principal, body: DecisionIn) -> WorkflowRun:
    if run.status != "waiting_for_human":
        raise HTTPException(409, "The run is not waiting for a decision")
    if run.pending_step_id is None or run.pending_step_id != body.step_id:
        raise HTTPException(409, "The decision names a different step than the one waiting")
    seq = (run.pending or {}).get("seq")
    # The same seq can pause more than once (a gated step, then the recorder going away at the
    # same seq); the pause's own nonce keeps the second decision from finding the first's job.
    nonce = (run.pending or {}).get("nonce")
    checkpoint = dict(run.checkpoint or {})
    decisions = dict(checkpoint.get("decisions") or {})
    decisions[str(body.step_id)] = {"decision": body.decision, "by": str(principal.user_id), "at": now().isoformat(), "reason": body.reason}
    checkpoint["decisions"] = decisions
    run.checkpoint = checkpoint
    run.status = "queued"
    run.pending = None
    run.pending_step_id = None
    session.commit()
    pause = f"{seq}:{nonce}" if nonce is not None else f"{seq}"
    _enqueue(session, run, principal.tenant_id, f"workflow_run:{run.id}:decision:{pause}:{body.decision}")
    return run


def stop(session: Session, run: WorkflowRun, principal, reason: str) -> WorkflowRun:
    if run.status in TERMINAL:
        return run
    run.stop_requested = True
    run.error = run.error or (f"stop requested: {reason}" if reason else "stop requested")
    if run.status == "waiting_for_harness" and run.harness_session_id is None:
        # Nothing has run and nobody holds it: the API may end it directly.
        run.status = "stopped"
        run.finished_at = now()
        run.pending = None
        agent_run = session.get(AgentRun, run.agent_run_id)
        if agent_run is not None:
            agent_run.status = "stopped"
            agent_run.finished_at = run.finished_at
            agent_run.error = run.error
        session.commit()
        return run
    session.commit()
    _enqueue(session, run, principal.tenant_id, f"workflow_run:{run.id}:stop")
    return run


# ---- recorder protocol ------------------------------------------------------------------------------


def presence(session: Session, principal, company_id: uuid.UUID, device_id: str, platform: str, capabilities: dict) -> HarnessDevice:
    device = session.scalar(select(HarnessDevice).where(HarnessDevice.user_id == principal.user_id, HarnessDevice.device_id == device_id))
    if device is None:
        device = HarnessDevice(user_id=principal.user_id, device_id=device_id, company_id=company_id)
        session.add(device)
    device.company_id = company_id
    device.platform = platform[:32]
    device.capabilities = {k: bool(v) for k, v in capabilities.items() if k in ("browser", "desktop")} | {
        k: v for k, v in capabilities.items() if k in ("recorder_version",)
    }
    device.last_seen_at = now()
    session.flush()
    return device


def _requester(user_id: uuid.UUID) -> dict:
    with platform_session() as platform:
        email = platform.scalar(select(User.email).where(User.id == user_id))
    return {"user_id": str(user_id), "email": email}


def offer_view(session: Session, run: WorkflowRun) -> dict:
    workflow = session.get(Workflow, run.workflow_id)
    version = session.get(WorkflowVersion, run.workflow_version_id)
    return {
        "id": None,
        "run_id": str(run.id),
        "status": "offered",
        "workflow": {
            "name": workflow.name if workflow else "",
            "version": version.number if version else 0,
            "goal": version.definition.get("goal") if version else "",
            "environment": "sandbox",
        },
        "harness_kinds": (run.pending or {}).get("harness_kinds", []),
        "limits": run.limits,
        "requested_by": _requester(run.requested_by),
        "offered_at": run.created_at.isoformat() if run.created_at else None,
        "expires_at": (run.pending or {}).get("expires_at"),
        "pending_step": None,
    }


def offers(session: Session, principal, company_id: uuid.UUID) -> list[WorkflowRun]:
    """Runs waiting for any recorder of this company. Expired offers fail here, lazily."""
    runs = session.scalars(
        select(WorkflowRun).where(
            WorkflowRun.company_id == company_id, WorkflowRun.status == "waiting_for_harness", WorkflowRun.harness_session_id.is_(None)
        )
    ).all()
    open_offers = []
    for run in runs:
        expires = (run.pending or {}).get("expires_at")
        if expires and datetime.fromisoformat(expires) < now():
            run.status, run.finished_at, run.error, run.pending = "failed", now(), "no recorder accepted the run in time", None
            agent_run = session.get(AgentRun, run.agent_run_id)
            if agent_run is not None:
                agent_run.status, agent_run.finished_at, agent_run.error = "failed", run.finished_at, run.error
            continue
        open_offers.append(run)
    session.commit()
    return open_offers


def active_session(session: Session, principal, device_id: str) -> HarnessSession | None:
    return session.scalar(
        select(HarnessSession)
        .where(HarnessSession.user_id == principal.user_id, HarnessSession.device_id == device_id, HarnessSession.status == "active")
        .order_by(HarnessSession.created_at.desc())
        .limit(1)
    )


def claim(session: Session, run: WorkflowRun, principal, body: ClaimIn) -> tuple[HarnessSession, str]:
    if run.status in TERMINAL:
        raise HTTPException(409, "The run has ended")
    if body.consent.version != CONSENT_VERSION:
        raise HTTPException(422, f"Consent version must be {CONSENT_VERSION}")
    needed = set((run.pending or {}).get("harness_kinds") or [])
    if run.harness_session_id is not None:
        existing = session.get(HarnessSession, run.harness_session_id)
        if existing is not None and existing.status == "active":
            if existing.device_id != body.device_id or existing.user_id != principal.user_id:
                if existing.lease_until > now():
                    raise HTTPException(409, "Another device holds this run")
                existing.status, existing.closed_at = "closed", now()
            else:
                existing.lease_until = now() + timedelta(seconds=settings.computer_use_lease_s)
                session.commit()
                return existing, existing.lease_token
    have = {k for k, v in body.capabilities.items() if v is True}
    if not needed <= have:
        raise HTTPException(409, f"This device cannot provide: {sorted(needed - have)}")
    token = secrets.token_urlsafe(32)[:64]
    hs = HarnessSession(
        workflow_run_id=run.id,
        device_id=body.device_id,
        user_id=principal.user_id,
        kinds=sorted(needed),
        capabilities=dict(body.capabilities),
        consent=body.consent.model_dump(mode="json"),
        lease_token=hashlib.sha256(token.encode()).hexdigest(),
        lease_until=now() + timedelta(seconds=settings.computer_use_lease_s),
        status="active",
    )
    session.add(hs)
    session.flush()
    run.harness_session_id = hs.id
    run.pending = {"kind": "claimed", "device_id": body.device_id, "harness_kinds": sorted(needed)}
    session.commit()
    _enqueue(session, run, principal.tenant_id, f"workflow_run:{run.id}:start")
    return hs, token


def _check_lease(hs: HarnessSession, device_id: str, token: str) -> None:
    if hs.status != "active" or hs.device_id != device_id or hs.lease_token != hashlib.sha256(token.encode()).hexdigest():
        raise HTTPException(403, "This device does not hold the session")
    if hs.lease_until < now():
        raise HTTPException(409, "lease_expired")


def session_view(session: Session, hs: HarnessSession, run: WorkflowRun, *, claim_step: bool) -> dict:
    workflow = session.get(Workflow, run.workflow_id)
    version = session.get(WorkflowVersion, run.workflow_version_id)
    pending_step = None
    if run.status == "waiting_for_harness" and run.pending_step_id is not None:
        step = session.get(HarnessStep, run.pending_step_id)
        if step is not None and step.status in ("pending", "claimed") and step.expires_at > now():
            if claim_step and step.status == "pending":
                step.status, step.claimed_at = "claimed", now()
            pending_step = step.request
    limits = run.limits or {}
    elapsed = (now() - run.started_at).total_seconds() if run.started_at else 0
    status = {"waiting_for_human": "paused_for_approval", "queued": "active", "running": "active", "waiting_for_harness": "active"}.get(
        run.status, run.status
    )
    return {
        "id": str(hs.id),
        "run_id": str(run.id),
        "workflow": {
            "name": workflow.name if workflow else "",
            "version": version.number if version else 0,
            "goal": version.definition.get("goal") if version else "",
            "environment": "sandbox",
        },
        "status": status if hs.status == "active" else "closed",
        "harness_kinds": hs.kinds,
        "limits": limits,
        "remaining": {
            "steps": max(0, int(limits.get("max_steps", 0)) - run.steps_used),
            "seconds": max(0, int(limits.get("max_runtime_seconds", 0)) - int(elapsed)),
            "cost_usd": str(max(0, float(limits.get("max_cost_usd", 0)) - float(run.cost_usd or 0))),
        },
        "pause_reason": (run.pending or {}).get("reason") if run.status == "waiting_for_human" else None,
        "lease": {"device_id": hs.device_id, "expires_at": hs.lease_until.isoformat()},
        "pending_step": pending_step,
    }


def poll(session: Session, hs: HarnessSession, run: WorkflowRun, *, device_id: str, token: str) -> dict:
    _check_lease(hs, device_id, token)
    hs.lease_until = now() + timedelta(seconds=settings.computer_use_lease_s)
    view = session_view(session, hs, run, claim_step=True)
    session.commit()
    return view


SCREENSHOT_TYPES = ("image/png", "image/jpeg", "image/webp")
# Set by the server when it stores a screenshot; a device may not supply them.
SERVER_EVIDENCE_FIELDS = ("artifact_key", "sha256", "bytes")


def screenshot_prefix(tenant_schema: str, run_id: uuid.UUID) -> str:
    return f"{tenant_schema}/computer-use/{run_id}/"


def screenshot_key(tenant_schema: str, run_id: uuid.UUID, evidence: dict | None) -> str | None:
    """The object key of a step's shared screenshot, only if it sits under this tenant's and
    run's own prefix. Anything else — a key a device wrote into its result, a key from
    another tenant — is treated as no screenshot."""
    key = (evidence or {}).get("artifact_key")
    if not isinstance(key, str) or not key.startswith(screenshot_prefix(tenant_schema, run_id)):
        return None
    return key


def store_evidence(tenant_schema: str, run: WorkflowRun, step: HarnessStep, evidence: dict) -> dict:
    """Keep the screenshot only when the employee consented; the planner never sees pixels.
    The storage fields are the server's: whatever a device sent under them is dropped."""
    for field in SERVER_EVIDENCE_FIELDS:
        evidence.pop(field, None)
    if evidence.get("content_type") not in SCREENSHOT_TYPES:
        evidence.pop("content_type", None)
    blob = evidence.pop("screenshot_base64", None)
    if not blob:
        return evidence
    try:
        data = base64.b64decode(blob, validate=True)
    except ValueError:
        evidence["error"] = "screenshot_not_base64"
        return evidence
    if len(data) > 2 * 1024 * 1024:
        evidence["error"] = "screenshot_too_large"
        return evidence
    digest = hashlib.sha256(data).hexdigest()
    key = f"{screenshot_prefix(tenant_schema, run.id)}{step.id}/{digest}"
    from vista.storage import s3_client

    s3_client().put_object(Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=str(evidence.get("content_type") or "image/png"))
    evidence["artifact_key"] = key
    evidence["sha256"] = digest
    evidence["bytes"] = len(data)
    return evidence


def post_result(session: Session, hs: HarnessSession, run: WorkflowRun, step: HarnessStep, principal, body: StepResultIn) -> dict:
    _check_lease(hs, body.device_id, body.lease_token)
    if step.workflow_run_id != run.id:
        raise HTTPException(404, "Step not found")
    if step.status in ("expired", "failed"):
        # The watchdog gave up on this step and the planner moved on; a late answer would make
        # the run resume against a plan that already recorded the step as not executed.
        raise HTTPException(409, "This step has expired; the run has moved on without it")
    if step.status == "done":
        return {"ok": True, "duplicate": True, "step_id": str(step.id)}
    result = body.model_dump(mode="json", exclude={"device_id", "lease_token"})
    if result.get("evidence"):
        consented = bool((hs.consent or {}).get("screenshots"))
        if not consented:
            result["evidence"].pop("screenshot_base64", None)
        result["evidence"] = store_evidence(principal.tenant_schema, run, step, dict(result["evidence"]))
    step.result = result
    step.status = "done"
    step.completed_at = now()
    hs.lease_until = now() + timedelta(seconds=settings.computer_use_lease_s)
    if run.status == "waiting_for_harness":
        run.status = "queued"
    session.commit()
    _enqueue(session, run, principal.tenant_id, f"workflow_run:{run.id}:resume:{step.seq}")
    return {"ok": True, "duplicate": False, "step_id": str(step.id)}


def close_session(session: Session, hs: HarnessSession, run: WorkflowRun, principal, body: SessionStopIn) -> dict:
    _check_lease(hs, body.device_id, body.lease_token)
    hs.status, hs.closed_at = "closed", now()
    for step in session.scalars(
        select(HarnessStep).where(HarnessStep.harness_session_id == hs.id, HarnessStep.status.in_(("pending", "claimed")))
    ).all():
        step.status = "failed"
        step.result = {"ok": False, "error": {"code": "session_closed", "message": body.reason or "the recorder ended the session"}}
        step.completed_at = now()
    session.commit()
    if run.status not in TERMINAL:
        stop(session, run, principal, body.reason or "the employee stopped the session")
    return {"ok": True, "status": run.status}
