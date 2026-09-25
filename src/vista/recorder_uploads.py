import base64
import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from taskmining import leakage
from taskmining.normalise import Vocabulary
from vista.agents.keys import agent_key_for
from vista.auth import Principal
from vista.automation.schemas import PlanGraph
from vista.config import settings
from vista.db import platform_session, set_tenant_search_path, tenant_session
from vista.jobs.queue import enqueue
from vista.models.platform import FirmCompany
from vista.models.tenant import AgentRun, Deal, DealMembership, Finding, RecorderReport, RecorderSubmission
from vista.recorder_analysis import MAX_QUESTIONS, finding_rows, public_report
from vista.storage import s3_client

ANALYSIS_JOB = "analyze_submission"
ANALYSIS_RUN_TYPE = "submission_analysis"

MAX_ACTIVITY_BYTES = 16 * 1024 * 1024  # full-detail activity carries titles, URLs and typed text
# What an activity artifact may carry. "activity-metadata-v1" is app names, event types, counts
# and timestamps only, checked on both ends against the recording's values and titles.
# "activity-full-v1" adds window titles, page URLs, control labels, typed text, clipboard
# contents and opened file names, as recorded on the device (after on-device redaction), so the
# analysis can say what a workflow does. Screenshots and video are never uploaded under either.
SHARING_POLICIES = ("activity-metadata-v1", "activity-full-v1")
FULL_DETAIL_POLICY = "activity-full-v1"
DETAIL_FIELDS = ("window_title", "url", "element", "text")
MAX_PLAN_BYTES = 1024 * 1024
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
# Consent versions the server accepts. A plan graph (the task's structure) is only accepted under
# `computer-use-v2`, whose consent card states that the full recording stays on the device and
# that the cloud receives the normalised graph plus activity metadata.
CONSENT_VERSIONS = ("activity-metadata-v1", "computer-use-v2")
PLAN_CONSENT_VERSION = "computer-use-v2"
DOCUMENT_TYPES = {
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroenabled.12",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkspaceChoice(StrictModel):
    id: uuid.UUID
    kind: Literal["company", "deal"]


class ArtifactSpec(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    kind: Literal["activity", "plan", "document"]
    filename: str = Field(min_length=1, max_length=255, pattern=r"^[^/\\\x00-\x1f]+$")
    content_type: str = Field(max_length=128)
    size_bytes: int = Field(strict=True, ge=1, le=MAX_FILE_BYTES)
    sha256: Digest

    @model_validator(mode="after")
    def supported(self):
        if self.kind == "activity":
            if self.id != "activity" or self.filename != "activity.json" or self.content_type != "application/json":
                raise ValueError("Invalid activity artifact")
            if self.size_bytes > MAX_ACTIVITY_BYTES:
                raise ValueError("Activity metadata exceeds the limit")
        elif self.kind == "plan":
            if self.id != "plan" or self.filename != "plan.json" or self.content_type != "application/json":
                raise ValueError("Invalid plan artifact")
            if self.size_bytes > MAX_PLAN_BYTES:
                raise ValueError("Plan graph exceeds the limit")
        elif self.id in ("activity", "plan") or DOCUMENT_TYPES.get(PurePosixPath(self.filename).suffix.lower()) != self.content_type:
            raise ValueError("Unsupported document type")
        return self


class SubmissionCreate(StrictModel):
    format_version: Literal[2]
    sharing_policy: Literal["activity-metadata-v1", "activity-full-v1"]
    consent: Literal[True]
    consent_version: Literal["activity-metadata-v1", "computer-use-v2"] = "activity-metadata-v1"
    device_id: uuid.UUID
    source_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    workspace: WorkspaceChoice
    started_at: AwareDatetime
    ended_at: AwareDatetime
    active_seconds: int = Field(strict=True, ge=0)
    artifacts: list[ArtifactSpec] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def bounded(self):
        if self.ended_at < self.started_at or self.active_seconds > (self.ended_at - self.started_at).total_seconds():
            raise ValueError("Invalid session duration")
        if len({a.id for a in self.artifacts}) != len(self.artifacts) or sum(a.kind == "activity" for a in self.artifacts) != 1:
            raise ValueError("Exactly one activity artifact and unique artifact IDs are required")
        if sum(a.kind == "plan" for a in self.artifacts) > 1:
            raise ValueError("At most one plan artifact is allowed")
        if any(a.kind == "plan" for a in self.artifacts) and self.consent_version != PLAN_CONSENT_VERSION:
            raise ValueError(f"Sharing a plan graph requires consent version {PLAN_CONSENT_VERSION}")
        if sum(a.size_bytes for a in self.artifacts) > MAX_TOTAL_BYTES:
            raise ValueError("Upload package exceeds the limit")
        return self


class ActivityEvent(StrictModel):
    timestamp: AwareDatetime
    event_type: Literal["focus", "click", "key", "scroll", "copy", "paste", "shortcut", "file"]
    app: str = Field(min_length=1, max_length=128)
    count: int = Field(strict=True, ge=1, le=10000)
    # Present only under activity-full-v1; a metadata-only package carrying any of them is refused.
    window_title: str | None = Field(default=None, min_length=1, max_length=255)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    element: str | None = Field(default=None, min_length=1, max_length=255)
    text: str | None = Field(default=None, min_length=1, max_length=4000)

    def detail_fields(self) -> list[str]:
        return [f for f in DETAIL_FIELDS if getattr(self, f) is not None]


class ActivityPayload(StrictModel):
    schema_version: Literal[1]
    events: list[ActivityEvent] = Field(max_length=50000)

    def check_policy(self, sharing_policy: str) -> None:
        """A metadata-only package must be exactly that; 'file' events name documents and are detail too."""
        if sharing_policy == FULL_DETAIL_POLICY:
            return
        for event in self.events:
            if event.detail_fields() or event.event_type == "file":
                raise ValueError("Activity payload carries detail that the sharing policy excludes")


def _workspaces(principal: Principal) -> tuple[list[dict], dict[tuple[str, str], dict]]:
    """The workspaces this user may upload to, and every (kind, id) key that names one.

    A tenant linked to an analyst company has exactly one workspace, the company. Its
    Deal keeps naming that same workspace: a recorder enrolled before the link stores
    `{kind: "deal", id: <deal>}` and must keep uploading after it, so the deal key is an
    alias of the company workspace rather than a workspace of its own."""
    with platform_session() as session:
        company = session.scalar(select(FirmCompany).where(FirmCompany.tenant_id == principal.tenant_id))
        if company is not None:
            if principal.role not in ("admin", "member") or company.status == "exited":
                return [], {}
            workspace = {"id": str(company.id), "kind": "company", "name": company.name, "canonical_company_id": str(company.id)}
            keys = {("company", workspace["id"]): workspace}
            if company.deal_id:
                keys[("deal", str(company.deal_id))] = workspace
            return [workspace], keys
    with tenant_session(principal.tenant_schema) as session:
        deals = session.scalars(
            select(Deal)
            .join(DealMembership, DealMembership.deal_id == Deal.id)
            .where(DealMembership.user_id == principal.user_id, DealMembership.role.in_(("owner", "member")))
            .order_by(Deal.name, Deal.id)
        ).all()
        workspaces = [{"id": str(d.id), "kind": "deal", "name": d.name, "canonical_company_id": None} for d in deals]
        return workspaces, {(w["kind"], w["id"]): w for w in workspaces}


def workspaces_for(principal: Principal) -> list[dict]:
    return _workspaces(principal)[0]


def workspace_keys(principal: Principal) -> set[tuple[str, str]]:
    """Every (kind, id) a stored submission may carry and still belong to this user's workspaces."""
    return set(_workspaces(principal)[1])


def resolve_workspace(principal: Principal, choice: WorkspaceChoice) -> dict:
    workspace = _workspaces(principal)[1].get((choice.kind, str(choice.id)))
    if workspace is None:
        raise HTTPException(404, "Upload workspace not found or access has been removed")
    return workspace


def manifest_hash(manifest: dict) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def submission_for(session: Session, principal: Principal, submission_id: uuid.UUID, *, lock: bool = False) -> RecorderSubmission:
    query = select(RecorderSubmission).where(RecorderSubmission.id == submission_id, RecorderSubmission.uploaded_by == principal.user_id)
    if lock:
        query = query.with_for_update()
    row = session.scalar(query)
    if row is None:
        raise HTTPException(404, "Submission not found")
    resolve_workspace(principal, WorkspaceChoice.model_validate(row.manifest["workspace"]))
    return row


def report_for(session: Session, row: RecorderSubmission) -> RecorderReport | None:
    return session.scalar(select(RecorderReport).where(RecorderReport.submission_id == row.id))


def public_submission(row: RecorderSubmission, report: RecorderReport | None = None, *, full_report: bool = False) -> dict:
    return {
        "id": str(row.id),
        "source_id": row.source_id,
        "device_id": str(row.device_id),
        "workspace": row.manifest["workspace"],
        "canonical_company_id": str(row.canonical_company_id) if row.canonical_company_id else None,
        "manifest_hash": row.manifest_hash,
        "upload_status": row.upload_status,
        "analysis_status": row.analysis_status,
        "analysis_run_id": str(row.analysis_run_id) if row.analysis_run_id else None,
        "analysis_error": row.analysis_error,
        "publication_status": report.status if report is not None else "draft",
        "report": public_report(report, full=full_report) if report is not None else None,
        "created_at": row.created_at,
        "receipt": {
            "submission_id": str(row.id),
            "manifest_hash": row.manifest_hash,
            "artifact_count": len(row.verified_artifacts),
            "verified_at": row.accepted_at,
        }
        if row.upload_status == "accepted"
        else None,
    }


def object_key(principal: Principal, row: RecorderSubmission, artifact: dict) -> str:
    return artifact_key(principal.tenant_schema, row, artifact)


def artifact_key(tenant_schema: str, row: RecorderSubmission, artifact: dict) -> str:
    return f"{tenant_schema}/recorder/submissions/{row.id}/{artifact['id']}/{artifact['sha256']}"


def read_artifact(tenant_schema: str, row: RecorderSubmission, artifact: dict, *, expected: dict | None = None) -> bytes:
    """Read one verified artifact back for analysis, re-checking size and digest
    against the manifest (and the receipt, when given) so a swapped object is
    never analysed."""
    key = artifact_key(tenant_schema, row, artifact)
    try:
        obj = s3_client().get_object(Bucket=settings.s3_bucket, Key=key)
        with obj["Body"] as stream:
            data = stream.read(artifact["size_bytes"] + 1)
    except (BotoCoreError, ClientError) as exc:
        raise RuntimeError(f"artifact {artifact['id']} could not be read from storage") from exc
    digest = hashlib.sha256(data).hexdigest()
    if len(data) != artifact["size_bytes"] or digest != artifact["sha256"] or (expected and expected.get("sha256") != digest):
        raise RuntimeError(f"artifact {artifact['id']} no longer matches its verified receipt")
    return data


def signed_uploads(principal: Principal, row: RecorderSubmission) -> list[dict]:
    if row.upload_status == "accepted":
        return []
    uploads = []
    client = s3_client()
    for artifact in row.manifest["artifacts"]:
        checksum = base64.b64encode(bytes.fromhex(artifact["sha256"])).decode()
        try:
            url = client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": settings.s3_bucket,
                    "Key": object_key(principal, row, artifact),
                    "ContentType": artifact["content_type"],
                    "ContentLength": artifact["size_bytes"],
                    "ChecksumSHA256": checksum,
                },
                ExpiresIn=900,
            )
        except (BotoCoreError, ClientError) as exc:
            raise HTTPException(503, "Upload storage is unavailable; retry later") from exc
        uploads.append(
            {
                "artifact_id": artifact["id"],
                "url": url,
                "headers": {
                    "Content-Type": artifact["content_type"],
                    "Content-Length": str(artifact["size_bytes"]),
                    "x-amz-checksum-sha256": checksum,
                },
            }
        )
    return uploads


def verify_objects(principal: Principal, row: RecorderSubmission) -> list[dict]:
    verified = []
    client = s3_client()
    for artifact in row.manifest["artifacts"]:
        key = object_key(principal, row, artifact)
        data = bytearray()
        digest = hashlib.sha256()
        size = 0
        try:
            obj = client.get_object(Bucket=settings.s3_bucket, Key=key)
            with obj["Body"] as stream:
                while chunk := stream.read(65536):
                    size += len(chunk)
                    if size > artifact["size_bytes"]:
                        raise HTTPException(409, "An uploaded artifact does not match its manifest; retry the upload")
                    digest.update(chunk)
                    if artifact["kind"] in ("activity", "plan"):
                        data.extend(chunk)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound"):
                raise HTTPException(409, "Required artifacts have not arrived; retry the upload") from exc
            raise HTTPException(503, "Upload storage is unavailable; retry later") from exc
        except BotoCoreError as exc:
            raise HTTPException(503, "Upload storage is unavailable; retry later") from exc
        if size != artifact["size_bytes"] or digest.hexdigest() != artifact["sha256"]:
            raise HTTPException(409, "An uploaded artifact does not match its manifest; retry the upload")
        if artifact["kind"] == "activity":
            try:
                activity = ActivityPayload.model_validate_json(data)
                manifest = SubmissionCreate.model_validate(row.manifest)
                activity.check_policy(manifest.sharing_policy)
                if any(not manifest.started_at <= event.timestamp <= manifest.ended_at for event in activity.events):
                    raise ValueError("Event outside session")
            except (ValidationError, ValueError) as exc:
                raise HTTPException(422, "Activity artifact is not valid session data for its sharing policy") from exc
        elif artifact["kind"] == "plan":
            try:
                graph = PlanGraph.model_validate_json(data)
            except ValidationError as exc:
                raise HTTPException(422, "Plan artifact is not a valid state graph") from exc
            report = plan_leakage(graph)
            if not report.ok:
                raise HTTPException(422, f"Plan artifact failed the privacy check: {report.summary()}")
        verified.append({"id": artifact["id"], "sha256": digest.hexdigest(), "size_bytes": size, "version_id": obj.get("VersionId")})
    return verified


def plan_leakage(graph: PlanGraph) -> leakage.Report:
    """The cloud's half of the leakage test: it never sees recorded values or titles, so it
    checks what it can — every token is one the normaliser produces and every name is made of
    the graph's own vocabulary. The device ran the full check against the recording before upload."""
    vocab = Vocabulary.from_json(graph.vocabulary.model_dump()) if graph.vocabulary else Vocabulary()
    return leakage.check(graph.model_dump(mode="json", exclude={"vocabulary"}), leakage.RecordingContext.build(vocab=vocab))


def _workspace_label(session: Session, row: RecorderSubmission) -> str | None:
    workspace = row.manifest["workspace"]
    if workspace["kind"] == "deal":
        return session.scalar(select(Deal.name).where(Deal.id == uuid.UUID(workspace["id"])))
    with platform_session() as platform:
        company = platform.get(FirmCompany, uuid.UUID(workspace["id"]))
        return company.name if company else None


def queue_analysis(session: Session, principal: Principal, row: RecorderSubmission) -> AgentRun | None:
    """Hand the accepted package to the Recording Reviewer through the job queue.
    One AgentRun envelope per attempt, created before the job (A2A contract);
    a no-op while an attempt is queued or running or a published report exists."""
    if row.upload_status != "accepted" or row.analysis_status in ("queued", "running"):
        return None
    report = report_for(session, row)
    if report is not None and report.status == "published":
        return None
    workspace = row.manifest["workspace"]
    run = AgentRun(
        job_id=uuid.uuid4(),  # replaced once the job row exists
        run_type=ANALYSIS_RUN_TYPE,
        agent_key=agent_key_for(ANALYSIS_RUN_TYPE),
        deal_id=uuid.UUID(workspace["id"]) if workspace["kind"] == "deal" else None,
        company=(_workspace_label(session, row) or "")[:64] or None,
        requested_by=row.uploaded_by,
    )
    session.add(run)
    session.flush()
    row.analysis_status, row.analysis_error, row.analysis_run_id = "queued", None, run.id
    session.commit()  # the envelope must be visible before the worker can claim the job
    set_tenant_search_path(session, principal.tenant_schema)  # a commit may hand back a different pooled connection
    with platform_session() as platform:
        job = enqueue(
            platform,
            principal.tenant_id,
            ANALYSIS_JOB,
            {"submission_id": str(row.id), "run_id": str(run.id), "parent_run_id": None},
            idempotency_key=f"submission:{row.id}:{ANALYSIS_JOB}:{run.id}",
        )
        platform.commit()
        job_id = job.id
    run.job_id = job_id
    session.commit()
    set_tenant_search_path(session, principal.tenant_schema)
    return run


def accept_submission(session: Session, principal: Principal, row: RecorderSubmission) -> dict:
    if row.upload_status != "accepted":
        row.verified_artifacts = verify_objects(principal, row)
        row.upload_status = "accepted"
        row.accepted_at = datetime.now(UTC)
        session.flush()
        session.commit()
        set_tenant_search_path(session, principal.tenant_schema)
    queue_analysis(session, principal, row)
    result = public_submission(row, report_for(session, row))
    session.commit()
    return result


class AnswersIn(StrictModel):
    answers: dict[Annotated[str, StringConstraints(pattern=r"^q[0-9]{1,2}$")], Annotated[str, StringConstraints(max_length=2000)]] = Field(
        min_length=1, max_length=MAX_QUESTIONS
    )


class PublishIn(StrictModel):
    consent: Literal[True]


def owned_draft_report(session: Session, principal: Principal, submission_id: uuid.UUID) -> tuple[RecorderSubmission, RecorderReport]:
    row = submission_for(session, principal, submission_id, lock=True)
    report = session.scalar(select(RecorderReport).where(RecorderReport.submission_id == row.id).with_for_update())
    if report is None:
        raise HTTPException(409, "This submission has no analysed report yet")
    return row, report


def record_answers(session: Session, principal: Principal, submission_id: uuid.UUID, body: AnswersIn) -> dict:
    row, report = owned_draft_report(session, principal, submission_id)
    if report.status != "draft":
        raise HTTPException(409, "Answers are frozen once a report is published")
    known = {q["id"] for q in report.questions}
    if not set(body.answers) <= known:
        raise HTTPException(422, "Unknown question")
    now = datetime.now(UTC)
    questions = []
    for q in report.questions:
        if q["id"] in body.answers:
            text = body.answers[q["id"]].strip()
            q = {**q, "answer": text or None, "answered_at": now.isoformat() if text else None}
        questions.append(q)
    report.questions = questions
    report.updated_at = now
    session.flush()
    result = public_submission(row, report, full_report=True)
    session.commit()
    return result


def publish_report(session: Session, principal: Principal, submission_id: uuid.UUID, body: PublishIn) -> dict:
    """The employee's explicit, second consent: the draft becomes visible to the
    workspace it was uploaded to. Idempotent; nothing else changes."""
    row, report = owned_draft_report(session, principal, submission_id)
    if row.analysis_status != "succeeded":
        raise HTTPException(409, "Only a completed analysis can be published")
    if report.status != "published":
        report.status = "published"
        report.published_at = datetime.now(UTC)
        report.published_by = principal.user_id
        report.updated_at = report.published_at
        record_findings(session, report)
        session.flush()
    result = public_submission(row, report, full_report=True)
    session.commit()
    return result


def record_findings(session: Session, report: RecorderReport) -> int:
    """The report's judged workflows become `findings` the workspace can act on — only
    now, on the employee's publish (the review gate), and at most once per run: a
    re-publish or a retry dedupes on kind + title. Returns how many were added."""
    if report.run_id is None:
        return 0
    run = session.get(AgentRun, report.run_id)
    existing = {(f.kind, f.title) for f in session.scalars(select(Finding).where(Finding.run_id == report.run_id))}
    added = 0
    for fields in finding_rows(report):
        key = (fields["kind"], fields["title"])
        if key in existing:
            continue
        existing.add(key)
        session.add(
            Finding(
                run_id=report.run_id,
                status="open",
                company=run.company if run else None,
                agent_key=(run.agent_key if run else None) or "recording_reviewer",
                **fields,
            )
        )
        added += 1
    return added


def readable_workspaces(session: Session, principal: Principal) -> set[tuple[str, str]]:
    """Workspaces whose published reports this user may read: the canonical
    company of their own tenant (any member/admin) and the deals they can view."""
    allowed: set[tuple[str, str]] = set()
    if principal.role in ("admin", "member"):
        with platform_session() as platform:
            company = platform.scalar(select(FirmCompany).where(FirmCompany.tenant_id == principal.tenant_id))
            if company is not None:
                allowed.add(("company", str(company.id)))
    deals = session.scalars(select(DealMembership.deal_id).where(DealMembership.user_id == principal.user_id)).all()
    if principal.role == "admin":
        deals = session.scalars(select(Deal.id)).all()
    allowed.update(("deal", str(d)) for d in deals)
    return allowed


def published_reports(session: Session, principal: Principal, *, limit: int, offset: int) -> list[dict]:
    allowed = readable_workspaces(session, principal)
    rows = session.scalars(
        select(RecorderReport)
        .where(RecorderReport.status == "published")
        .order_by(RecorderReport.published_at.desc(), RecorderReport.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return [public_report(r, full=False) for r in rows if (r.workspace["kind"], r.workspace["id"]) in allowed]


def readable_report(session: Session, principal: Principal, report_id: uuid.UUID) -> RecorderReport:
    report = session.get(RecorderReport, report_id)
    if report is None:
        raise HTTPException(404, "Report not found")
    if report.uploaded_by == principal.user_id:
        submission_for(session, principal, report.submission_id)  # still needs current upload access
        return report
    if report.status != "published" or (report.workspace["kind"], report.workspace["id"]) not in readable_workspaces(session, principal):
        raise HTTPException(404, "Report not found")
    return report
