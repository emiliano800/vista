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

from vista.auth import Principal
from vista.config import settings
from vista.db import platform_session, tenant_session
from vista.models.platform import FirmCompany
from vista.models.tenant import Deal, DealMembership, RecorderSubmission
from vista.storage import s3_client

MAX_ACTIVITY_BYTES = 4 * 1024 * 1024
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
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
    kind: Literal["activity", "document"]
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
        elif self.id == "activity" or DOCUMENT_TYPES.get(PurePosixPath(self.filename).suffix.lower()) != self.content_type:
            raise ValueError("Unsupported document type")
        return self


class SubmissionCreate(StrictModel):
    format_version: Literal[2]
    sharing_policy: Literal["activity-metadata-v1"]
    consent: Literal[True]
    device_id: uuid.UUID
    source_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    workspace: WorkspaceChoice
    started_at: AwareDatetime
    ended_at: AwareDatetime
    active_seconds: int = Field(strict=True, ge=0)
    artifacts: list[ArtifactSpec] = Field(min_length=1, max_length=11)

    @model_validator(mode="after")
    def bounded(self):
        if self.ended_at < self.started_at or self.active_seconds > (self.ended_at - self.started_at).total_seconds():
            raise ValueError("Invalid session duration")
        if len({a.id for a in self.artifacts}) != len(self.artifacts) or sum(a.kind == "activity" for a in self.artifacts) != 1:
            raise ValueError("Exactly one activity artifact and unique artifact IDs are required")
        if sum(a.size_bytes for a in self.artifacts) > MAX_TOTAL_BYTES:
            raise ValueError("Upload package exceeds the limit")
        return self


class ActivityEvent(StrictModel):
    timestamp: AwareDatetime
    event_type: Literal["focus", "click", "key", "scroll", "copy", "paste", "shortcut"]
    app: str = Field(min_length=1, max_length=128)
    count: int = Field(strict=True, ge=1, le=10000)


class ActivityPayload(StrictModel):
    schema_version: Literal[1]
    events: list[ActivityEvent] = Field(max_length=50000)


def workspaces_for(principal: Principal) -> list[dict]:
    with platform_session() as session:
        company = session.scalar(select(FirmCompany).where(FirmCompany.tenant_id == principal.tenant_id))
        if company is not None:
            if principal.role not in ("admin", "member") or company.status == "exited":
                return []
            return [{"id": str(company.id), "kind": "company", "name": company.name, "canonical_company_id": str(company.id)}]
    with tenant_session(principal.tenant_schema) as session:
        deals = session.scalars(
            select(Deal)
            .join(DealMembership, DealMembership.deal_id == Deal.id)
            .where(DealMembership.user_id == principal.user_id, DealMembership.role.in_(("owner", "member")))
            .order_by(Deal.name, Deal.id)
        ).all()
        return [{"id": str(d.id), "kind": "deal", "name": d.name, "canonical_company_id": None} for d in deals]


def resolve_workspace(principal: Principal, choice: WorkspaceChoice) -> dict:
    for workspace in workspaces_for(principal):
        if workspace["id"] == str(choice.id) and workspace["kind"] == choice.kind:
            return workspace
    raise HTTPException(404, "Upload workspace not found or access has been removed")


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


def public_submission(row: RecorderSubmission) -> dict:
    return {
        "id": str(row.id),
        "source_id": row.source_id,
        "device_id": str(row.device_id),
        "workspace": row.manifest["workspace"],
        "canonical_company_id": str(row.canonical_company_id) if row.canonical_company_id else None,
        "manifest_hash": row.manifest_hash,
        "upload_status": row.upload_status,
        "analysis_status": "not_started",
        "publication_status": "draft",
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
    return f"{principal.tenant_schema}/recorder/submissions/{row.id}/{artifact['id']}/{artifact['sha256']}"


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
                    if artifact["kind"] == "activity":
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
                if any(not manifest.started_at <= event.timestamp <= manifest.ended_at for event in activity.events):
                    raise ValueError("Event outside session")
            except (ValidationError, ValueError) as exc:
                raise HTTPException(422, "Activity artifact is not valid metadata-only session data") from exc
        verified.append({"id": artifact["id"], "sha256": digest.hexdigest(), "size_bytes": size, "version_id": obj.get("VersionId")})
    return verified


def accept_submission(session: Session, principal: Principal, row: RecorderSubmission) -> dict:
    if row.upload_status != "accepted":
        row.verified_artifacts = verify_objects(principal, row)
        row.upload_status = "accepted"
        row.accepted_at = datetime.now(UTC)
        session.flush()
    result = public_submission(row)
    session.commit()
    return result
