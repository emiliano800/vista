import copy
import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from typing import Literal

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from vista.auth import Principal, current_principal
from vista.config import settings
from vista.db import tenant_session
from vista.ingestion import SCHEMAS, analyze, apply_mappings, parse_files
from vista.models.tenant import ImportBatch
from vista.permissions import require_deal_role
from vista.storage import s3_client

router = APIRouter(tags=["imports"])


class ImportFile(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1, max_length=7_000_000, repr=False)


class Upload(BaseModel):
    files: list[ImportFile] = Field(min_length=1, max_length=12)
    as_of: date


class Mapping(BaseModel):
    id: str = Field(max_length=8)
    kind: str = Field(max_length=32)
    mapping: dict[str, str]


class Commit(BaseModel):
    tables: list[Mapping] = Field(min_length=1, max_length=20)


class Decision(BaseModel):
    status: Literal["open", "reviewed", "dismissed"]


def event(action, principal, **extra):
    return {"action": action, "by": str(principal.user_id), "at": datetime.now(UTC).isoformat(), **extra}


def output(batch, *, detail=False):
    result = {
        "id": str(batch.id),
        "deal_id": str(batch.deal_id),
        "status": batch.status,
        "as_of": batch.as_of,
        "created_at": batch.created_at.isoformat(),
        "analysis": batch.analysis,
        "files": list(dict.fromkeys(t["filename"] for t in batch.tables)),
        "record_count": sum(len(t["records"]) for t in batch.tables),
    }
    if detail:
        result["tables"] = batch.tables
        result["schemas"] = SCHEMAS
        result["events"] = batch.events
    return result


def get_batch(session, batch_id, principal, role="viewer", lock=False):
    query = select(ImportBatch).where(ImportBatch.id == batch_id)
    batch = session.scalar(query.with_for_update() if lock else query)
    if batch is None:
        raise HTTPException(404, "Import not found")
    require_deal_role(session, batch.deal_id, principal.user_id, role)
    return batch


@router.get("/deals/{deal_id}/imports")
def list_imports(deal_id: uuid.UUID, principal: Principal = Depends(current_principal)):
    with tenant_session(principal.tenant_schema) as session:
        role = require_deal_role(session, deal_id, principal.user_id, "viewer")
        batches = session.scalars(
            select(ImportBatch).where(ImportBatch.deal_id == deal_id).order_by(ImportBatch.created_at.desc()).limit(100)
        )
        return {"role": role, "imports": [output(batch) for batch in batches]}


@router.post("/deals/{deal_id}/imports", status_code=201)
def upload(deal_id: uuid.UUID, body: Upload, principal: Principal = Depends(current_principal)):
    with tenant_session(principal.tenant_schema) as session:
        require_deal_role(session, deal_id, principal.user_id, "member")
        files = [f.model_dump() for f in body.files]
        try:
            tables = parse_files(files)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        bundle = json.dumps({"files": sorted(files, key=lambda f: f["name"]), "as_of": body.as_of.isoformat()}, sort_keys=True).encode()
        digest = hashlib.sha256(bundle).hexdigest()
        session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(hashlib.sha256(f"{deal_id}:{digest}".encode()).hexdigest()[:15], 16)}
        )
        existing = session.scalar(select(ImportBatch).where(ImportBatch.deal_id == deal_id, ImportBatch.content_hash == digest))
        if existing:
            return output(existing, detail=True)
        batch_id = uuid.uuid4()
        key = f"{principal.tenant_schema}/deals/{deal_id}/imports/{batch_id}/source.json"
        try:
            s3_client().put_object(Bucket=settings.s3_bucket, Key=key, Body=bundle, ContentType="application/json")
        except (BotoCoreError, ClientError) as exc:
            raise HTTPException(503, "Source storage is unavailable. Your import has not been saved; try again.") from exc
        batch = ImportBatch(
            id=batch_id,
            deal_id=deal_id,
            uploaded_by=principal.user_id,
            content_hash=digest,
            s3_key=key,
            as_of=body.as_of.isoformat(),
            tables=tables,
            analysis={},
            events=[event("uploaded", principal)],
        )
        session.add(batch)
        session.commit()
        return output(batch, detail=True)


@router.get("/imports/{batch_id}")
def detail(batch_id: uuid.UUID, principal: Principal = Depends(current_principal)):
    with tenant_session(principal.tenant_schema) as session:
        return output(get_batch(session, batch_id, principal), detail=True)


@router.post("/imports/{batch_id}/commit")
def commit(batch_id: uuid.UUID, body: Commit, principal: Principal = Depends(current_principal)):
    with tenant_session(principal.tenant_schema) as session:
        batch = get_batch(session, batch_id, principal, "member", lock=True)
        if batch.status == "completed":
            return output(batch, detail=True)
        try:
            choices = [t.model_dump() for t in body.tables]
            if len({t["id"] for t in choices}) != len(choices):
                raise ValueError("Each table must have exactly one mapping.")
            tables = apply_mappings(copy.deepcopy(batch.tables), choices)
            analysis = analyze(tables, date.fromisoformat(batch.as_of))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        batch.tables, batch.analysis, batch.status = tables, analysis, "completed"
        batch.events = [*batch.events, event("confirmed_and_analyzed", principal, mappings=choices)]
        session.commit()
        return output(batch, detail=True)


@router.post("/imports/{batch_id}/findings/{finding_id}")
def decide(batch_id: uuid.UUID, finding_id: str, body: Decision, principal: Principal = Depends(current_principal)):
    with tenant_session(principal.tenant_schema) as session:
        batch = get_batch(session, batch_id, principal, "member", lock=True)
        analysis = copy.deepcopy(batch.analysis)
        finding = next((f for f in analysis.get("findings", []) if f["id"] == finding_id), None)
        if finding is None:
            raise HTTPException(404, "Finding not found")
        if finding["status"] != body.status:
            finding["status"] = body.status
            batch.analysis = analysis
            batch.events = [*batch.events, event("finding_reviewed", principal, finding_id=finding_id, status=body.status)]
            session.commit()
        return output(batch, detail=True)


@router.get("/imports/{batch_id}/export")
def export(batch_id: uuid.UUID, principal: Principal = Depends(current_principal)):
    with tenant_session(principal.tenant_schema) as session:
        batch = get_batch(session, batch_id, principal)
        return Response(
            json.dumps(output(batch, detail=True), indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="vista-import-{batch.id}.json"'},
        )
