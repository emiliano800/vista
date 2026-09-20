import csv
import hashlib
import io
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text

from vista.auth import Principal, current_principal
from vista.config import settings
from vista.db import platform_session, tenant_session
from vista.jobs.queue import enqueue
from vista.models.tenant import AgentRun, Deal, Recording, RecordingReviewItem
from vista.permissions import require_deal_role
from vista.recordings import RecordingUpload, parse_evidence
from vista.review import (
    CONFIDENCE_THRESHOLD,
    RESOLVED_STATUSES,
    DecisionIn,
    SectionsIn,
    apply_decision,
    public_review,
)
from vista.storage import presigned_download_url, presigned_upload_url, s3_client

router = APIRouter(tags=["recordings"])


def public_recording(record: Recording):
    return {
        "id": str(record.id),
        "deal_id": str(record.deal_id),
        "source_id": record.source_id,
        "started_at": record.started_at,
        "ended_at": record.ended_at,
        "active_seconds": record.active_seconds,
        "manifest": record.manifest,
        "summary": record.summary,
        "sections": record.sections or {},
        "media": sorted((record.media or {}).keys()),
        "files": record.files or [],
        "uploaded_at": record.updated_at,
        "analysis_source": "local_taskmining",
        "content_hash": record.content_hash,
    }


@router.post("/deals/{deal_id}/recordings")
def upload_recording(deal_id: uuid.UUID, body: RecordingUpload, principal: Principal = Depends(current_principal)):
    payload = body.model_dump(mode="json", by_alias=True)
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    digest = hashlib.sha256(data).hexdigest()
    with tenant_session(principal.tenant_schema) as session:
        require_deal_role(session, deal_id, principal.user_id, "member")
        # Serialize retries for this user's source session, without locking other uploads.
        lock = f"{principal.tenant_id}:{deal_id}:{principal.user_id}:{body.manifest.recording_id}"
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": lock})
        record = session.scalar(
            select(Recording).where(
                Recording.deal_id == deal_id, Recording.uploaded_by == principal.user_id, Recording.source_id == body.manifest.recording_id
            )
        )
        if record and record.content_hash == digest:
            return public_recording(record)
        rid = record.id if record else uuid.uuid4()
        key = f"{principal.tenant_schema}/deals/{deal_id}/recordings/{rid}/{digest}.json"
        try:
            s3_client().put_object(Bucket=settings.s3_bucket, Key=key, Body=data, ContentType="application/json")
        except (BotoCoreError, ClientError) as exc:
            raise HTTPException(503, "Report storage is unavailable; retry the upload") from exc
        if record is None:
            record = Recording(id=rid, deal_id=deal_id, uploaded_by=principal.user_id, source_id=body.manifest.recording_id)
            session.add(record)
        record.started_at, record.ended_at = body.manifest.started_at, body.manifest.ended_at
        record.active_seconds = body.manifest.active_seconds
        record.manifest = {**payload["manifest"], "name": body.name, "summary_text": body.summary_text}
        record.summary = payload["summary"]
        record.sections = payload["sections"]
        prev = {f.get("id"): f for f in record.files or []}
        record.files = [{**f, "extraction": _kept_extraction(prev.get(f["id"]), f)} for f in payload["files"]]
        record.s3_key, record.content_hash = key, digest
        record.updated_at = datetime.now(UTC)
        session.commit()
        return public_recording(record)


def _kept_extraction(old: dict | None, new: dict) -> dict | None:
    """Extraction is only valid for the exact bytes it ran on: same snapshot key and sha256."""
    if not old or old.get("sha256") != new.get("sha256") or old.get("snapshot") != new.get("snapshot"):
        return None
    return old.get("extraction")


def _uploaded_size(key: str) -> int | None:
    try:
        return int(s3_client().head_object(Bucket=settings.s3_bucket, Key=key)["ContentLength"])
    except (BotoCoreError, ClientError, KeyError, ValueError):
        return None


@router.get("/deals/{deal_id}/recordings")
def list_recordings(
    deal_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(current_principal),
):
    with tenant_session(principal.tenant_schema) as session:
        require_deal_role(session, deal_id, principal.user_id, "viewer")
        rows = session.scalars(
            select(Recording)
            .where(Recording.deal_id == deal_id)
            .order_by(Recording.started_at.desc(), Recording.id)
            .offset(offset)
            .limit(limit)
        ).all()
        return [public_recording(r) for r in rows]


def authorized_recording(recording_id, principal):
    with tenant_session(principal.tenant_schema) as session:
        record = session.get(Recording, recording_id)
        if record is None:
            raise HTTPException(404, "Recording not found")
        require_deal_role(session, record.deal_id, principal.user_id, "viewer")
        return record


def load_bundle(record):
    try:
        obj = s3_client().get_object(Bucket=settings.s3_bucket, Key=record.s3_key)
        with obj["Body"] as stream:
            return json.loads(stream.read())
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(503, "Report storage is unavailable; try again") from exc


@router.get("/recordings/{recording_id}")
def get_recording(recording_id: uuid.UUID, principal: Principal = Depends(current_principal)):
    return public_recording(authorized_recording(recording_id, principal))


@router.get("/recordings/{recording_id}/evidence")
def evidence(
    recording_id: uuid.UUID,
    activity: str | None = Query(None, max_length=4096),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    principal: Principal = Depends(current_principal),
):
    bundle = load_bundle(authorized_recording(recording_id, principal))
    rows = [{"row": i + 1, **r} for i, r in enumerate(parse_evidence(bundle["event_log_csv"]))]
    if activity is not None:
        rows = [r for r in rows if r["activity"] == activity]
    return {"total": len(rows), "rows": rows[offset : offset + limit]}


@router.get("/recordings/{recording_id}/download")
def download(
    recording_id: uuid.UUID, format: str = Query("json", pattern="^(json|csv)$"), principal: Principal = Depends(current_principal)
):
    bundle = load_bundle(authorized_recording(recording_id, principal))
    if format == "csv":
        # Make spreadsheet exports inert; preserve the exact evidence in JSON.
        source = list(csv.reader(io.StringIO(bundle["event_log_csv"])))
        out = io.StringIO()
        csv.writer(out).writerows(
            [["'" + cell if cell.lstrip().startswith(("=", "+", "-", "@")) else cell for cell in row] for row in source]
        )
        data, media = out.getvalue(), "text/csv"
    else:
        data, media = json.dumps(bundle, ensure_ascii=False), "application/json"
    return Response(
        data,
        media_type=media,
        headers={"Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="vista-{recording_id}.{format}"'},
    )


# ---- media: screenshots, screen video and raw events go straight to object storage ----
# The recorder asks for signed PUT URLs (the API body limit is far below a video), uploads,
# then keeps nothing on the employee's machine. Only the uploader may add media; viewers read.

MEDIA_NAME = re.compile(r"^(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$")
MEDIA_MAX_BYTES = 2 * 1024 * 1024 * 1024
MEDIA_MAX_FILES = 5000


class MediaFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=255)
    content_type: str = Field(max_length=128, pattern=r"^[a-z]+/[a-z0-9.+-]+$")
    size_bytes: int = Field(ge=0, le=MEDIA_MAX_BYTES)


class MediaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: Annotated[list[MediaFile], Field(max_length=500)]


@router.post("/recordings/{recording_id}/media")
def request_media_uploads(recording_id: uuid.UUID, body: MediaRequest, principal: Principal = Depends(current_principal)):
    with tenant_session(principal.tenant_schema) as session:
        record = session.get(Recording, recording_id)
        if record is None:
            raise HTTPException(404, "Recording not found")
        require_deal_role(session, record.deal_id, principal.user_id, "member")
        if record.uploaded_by != principal.user_id:
            raise HTTPException(403, "Only the employee who recorded a session can upload its files")
        media = dict(record.media or {})
        uploads = []
        for f in body.files:
            if not MEDIA_NAME.match(f.name):
                raise HTTPException(422, f"Invalid file name: {f.name!r}")
            key = f"{principal.tenant_schema}/deals/{record.deal_id}/recordings/{record.id}/media/{f.name}"
            media[f.name] = {"key": key, "content_type": f.content_type, "size_bytes": f.size_bytes}
            try:
                uploads.append({"name": f.name, "url": presigned_upload_url(key, f.content_type)})
            except (BotoCoreError, ClientError) as exc:
                raise HTTPException(503, "File storage is unavailable; retry the upload") from exc
        if len(media) > MEDIA_MAX_FILES:
            raise HTTPException(422, f"A recording may hold at most {MEDIA_MAX_FILES} files")
        record.media = media
        record.updated_at = datetime.now(UTC)
        session.commit()
        return {"uploads": uploads}


@router.post("/recordings/{recording_id}/media/complete")
def media_complete(recording_id: uuid.UUID, principal: Principal = Depends(current_principal)):
    """The recorder has finished its PUTs. Documents whose snapshot arrived are
    queued for text extraction (worker); the rest are marked as missing."""
    with tenant_session(principal.tenant_schema) as session:
        record = session.get(Recording, recording_id)
        if record is None:
            raise HTTPException(404, "Recording not found")
        require_deal_role(session, record.deal_id, principal.user_id, "member")
        if record.uploaded_by != principal.user_id:
            raise HTTPException(403, "Only the employee who recorded a session can upload its files")
        media = record.media or {}
        files, queued = [], 0
        for f in record.files or []:
            f = dict(f)
            item = media.get(f["snapshot"]) if f.get("snapshot") else None
            if item and _uploaded_size(item["key"]) == item.get("size_bytes"):
                if not (f.get("extraction") or {}).get("status") == "done":
                    f["extraction"] = {"status": "queued"}
                    queued += 1
            elif f.get("snapshot"):
                f["extraction"] = {"status": "missing"}
            files.append(f)
        record.files = files
        record.updated_at = datetime.now(UTC)
        session.commit()
        now = record.updated_at
    if queued:
        with platform_session() as psession:
            enqueue(
                psession,
                principal.tenant_id,
                "extract_recording_files",
                {"recording_id": str(recording_id)},
                idempotency_key=f"files:{recording_id}:{now.isoformat()}",
            )
            psession.commit()
    return {"files": files, "queued": queued}


@router.get("/recordings/{recording_id}/files")
def list_files(recording_id: uuid.UUID, principal: Principal = Depends(current_principal)):
    return authorized_recording(recording_id, principal).files or []


@router.get("/recordings/{recording_id}/files/{file_id}/text")
def get_file_text(recording_id: uuid.UUID, file_id: str, principal: Principal = Depends(current_principal)):
    """Redirects to the extracted JSON (sheets/paragraphs/slides/pages) of one document."""
    record = authorized_recording(recording_id, principal)
    item = next((f for f in record.files or [] if f.get("id") == file_id), None)
    if item is None:
        raise HTTPException(404, "File not found")
    key = (item.get("extraction") or {}).get("key")
    if not key:
        raise HTTPException(409, "Text has not been extracted yet")
    try:
        url = presigned_download_url(key)
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(503, "File storage is unavailable; try again") from exc
    return RedirectResponse(url, status_code=307, headers={"Cache-Control": "no-store"})


@router.get("/recordings/{recording_id}/media")
def list_media(recording_id: uuid.UUID, principal: Principal = Depends(current_principal)):
    record = authorized_recording(recording_id, principal)
    return [{"name": n, "content_type": m["content_type"], "size_bytes": m["size_bytes"]} for n, m in sorted((record.media or {}).items())]


@router.get("/recordings/{recording_id}/media/{name:path}")
def get_media(recording_id: uuid.UUID, name: str, principal: Principal = Depends(current_principal)):
    record = authorized_recording(recording_id, principal)
    item = (record.media or {}).get(name)
    if item is None:
        raise HTTPException(404, "File not found")
    try:
        url = presigned_download_url(item["key"])
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(503, "File storage is unavailable; try again") from exc
    return RedirectResponse(url, status_code=307, headers={"Cache-Control": "no-store"})


# ---- employee review: AI explanation per section, approve / fix / explain --------


def review_items(session, recording_id):
    return session.scalars(
        select(RecordingReviewItem).where(RecordingReviewItem.recording_id == recording_id).order_by(RecordingReviewItem.created_at)
    ).all()


def latest_review_run(session, recording_id) -> AgentRun | None:
    return session.scalar(
        select(AgentRun)
        .where(AgentRun.recording_id == recording_id, AgentRun.run_type == "recording_review")
        .order_by(AgentRun.created_at.desc())
        .limit(1)
    )


@router.put("/recordings/{recording_id}/review/sections")
def submit_sections(recording_id: uuid.UUID, body: SectionsIn, principal: Principal = Depends(current_principal)):
    """The recorder describes each video section (redacted on the device); the
    worker asks the model to explain them. Resolved items are never redone."""
    with tenant_session(principal.tenant_schema) as session:
        record = session.get(Recording, recording_id)
        if record is None:
            raise HTTPException(404, "Recording not found")
        require_deal_role(session, record.deal_id, principal.user_id, "member")
        if record.uploaded_by != principal.user_id:
            raise HTTPException(403, "Only the employee who recorded a session can submit it for review")
        existing = {i.item_id: i for i in review_items(session, recording_id)}
        now = datetime.now(UTC)
        queued = 0
        for sec in body.items:
            item = existing.get(sec.id)
            if item is None:
                item = RecordingReviewItem(recording_id=recording_id, item_id=sec.id, threshold=CONFIDENCE_THRESHOLD)
                session.add(item)
            elif item.status in RESOLVED_STATUSES or (item.status not in {"pending", "failed"} and not body.force):
                continue
            item.section = sec.section.model_dump(mode="json")
            item.prompt = sec.description
            item.status = "pending"
            item.error = None
            item.updated_at = now
            queued += 1
        session.flush()
        run = latest_review_run(session, recording_id)
        if queued:
            # One Recording Reviewer run per submission so the sections' model calls
            # land in the same ledger as every other agent.
            run = AgentRun(
                job_id=uuid.uuid4(),
                run_type="recording_review",
                agent_key="recording_reviewer",
                recording_id=recording_id,
                deal_id=record.deal_id,
                company=session.scalar(select(Deal.name).where(Deal.id == record.deal_id)),
                requested_by=principal.user_id,
            )
            session.add(run)
        items = review_items(session, recording_id)
        session.commit()  # the run must exist before the worker can pick up its job
        out = public_review(items, run)
    if queued:
        with platform_session() as psession:
            enqueue(
                psession,
                principal.tenant_id,
                "explain_recording",
                {"recording_id": str(recording_id), "run_id": str(run.id)},
                idempotency_key=f"{recording_id}:{now.isoformat()}",
            )
            psession.commit()
    return out


@router.get("/recordings/{recording_id}/review")
def get_review(recording_id: uuid.UUID, principal: Principal = Depends(current_principal)):
    record = authorized_recording(recording_id, principal)
    with tenant_session(principal.tenant_schema) as session:
        return public_review(review_items(session, record.id), latest_review_run(session, record.id))


@router.post("/recordings/{recording_id}/review/{item_id}")
def decide(recording_id: uuid.UUID, item_id: str, body: DecisionIn, principal: Principal = Depends(current_principal)):
    with tenant_session(principal.tenant_schema) as session:
        record = session.get(Recording, recording_id)
        if record is None:
            raise HTTPException(404, "Recording not found")
        require_deal_role(session, record.deal_id, principal.user_id, "member")
        item = session.scalar(
            select(RecordingReviewItem).where(RecordingReviewItem.recording_id == recording_id, RecordingReviewItem.item_id == item_id)
        )
        if item is None:
            raise HTTPException(404, "Review item not found")
        try:
            apply_decision(item, body, principal.user_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        items = review_items(session, recording_id)
        run = latest_review_run(session, recording_id)
        session.commit()
        return public_review(items, run)
