import csv
import hashlib
import io
import json
import uuid
from datetime import UTC, datetime

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select, text

from vista.auth import Principal, current_principal
from vista.config import settings
from vista.db import platform_session, tenant_session
from vista.jobs.queue import enqueue
from vista.models.tenant import Recording, RecordingReviewItem
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
from vista.storage import s3_client

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
        record.manifest, record.summary = payload["manifest"], payload["summary"]
        record.s3_key, record.content_hash = key, digest
        record.updated_at = datetime.now(UTC)
        session.commit()
        return public_recording(record)


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


# ---- employee review: AI explanation per section, approve / fix / explain --------


def review_items(session, recording_id):
    return session.scalars(
        select(RecordingReviewItem).where(RecordingReviewItem.recording_id == recording_id).order_by(RecordingReviewItem.created_at)
    ).all()


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
        items = review_items(session, recording_id)
        session.commit()
    if queued:
        with platform_session() as psession:
            enqueue(
                psession,
                principal.tenant_id,
                "explain_recording",
                {"recording_id": str(recording_id)},
                idempotency_key=f"{recording_id}:{now.isoformat()}",
            )
            psession.commit()
    return public_review(items)


@router.get("/recordings/{recording_id}/review")
def get_review(recording_id: uuid.UUID, principal: Principal = Depends(current_principal)):
    record = authorized_recording(recording_id, principal)
    with tenant_session(principal.tenant_schema) as session:
        return public_review(review_items(session, record.id))


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
        session.commit()
        return public_review(items)
