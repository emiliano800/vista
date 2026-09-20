import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from vista.api.schemas import (
    DealCreate,
    DealOut,
    DocumentCreate,
    DocumentCreated,
    DocumentOut,
)
from vista.auth import Principal, current_principal
from vista.db import tenant_session
from vista.models.tenant import Deal, DealMembership, Document
from vista.permissions import require_deal_role
from vista.storage import presigned_download_url, presigned_upload_url

router = APIRouter(tags=["deals"])


@router.post("/deals", response_model=DealOut, status_code=201)
def create_deal(body: DealCreate, principal: Principal = Depends(current_principal)) -> DealOut:
    with tenant_session(principal.tenant_schema) as session:
        deal = Deal(name=body.name, created_by=principal.user_id)
        session.add(deal)
        session.flush()
        session.add(DealMembership(deal_id=deal.id, user_id=principal.user_id, role="owner"))
        session.commit()
        return DealOut(id=deal.id, name=deal.name, created_at=deal.created_at)


@router.get("/deals", response_model=list[DealOut])
def list_deals(principal: Principal = Depends(current_principal)) -> list[DealOut]:
    with tenant_session(principal.tenant_schema) as session:
        deals = session.scalars(
            select(Deal).join(DealMembership, DealMembership.deal_id == Deal.id).where(DealMembership.user_id == principal.user_id)
        ).all()
        return [DealOut(id=d.id, name=d.name, created_at=d.created_at) for d in deals]


@router.post("/deals/{deal_id}/documents", response_model=DocumentCreated, status_code=201)
def create_document(deal_id: uuid.UUID, body: DocumentCreate, principal: Principal = Depends(current_principal)) -> DocumentCreated:
    with tenant_session(principal.tenant_schema) as session:
        require_deal_role(session, deal_id, principal.user_id, "member")
        doc = Document(
            deal_id=deal_id,
            filename=body.filename,
            s3_key="",  # set below once we have the id
            content_type=body.content_type,
            size_bytes=body.size_bytes,
            uploaded_by=principal.user_id,
        )
        session.add(doc)
        session.flush()
        doc.s3_key = f"{principal.tenant_schema}/deals/{deal_id}/documents/{doc.id}/{body.filename}"
        session.commit()
        url = presigned_upload_url(doc.s3_key, body.content_type)
        return DocumentCreated(id=doc.id, filename=doc.filename, upload_url=url)


@router.get("/deals/{deal_id}/documents", response_model=list[DocumentOut])
def list_documents(deal_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> list[DocumentOut]:
    with tenant_session(principal.tenant_schema) as session:
        require_deal_role(session, deal_id, principal.user_id, "viewer")
        docs = session.scalars(select(Document).where(Document.deal_id == deal_id)).all()
        return [
            DocumentOut(
                id=d.id,
                deal_id=d.deal_id,
                filename=d.filename,
                content_type=d.content_type,
                size_bytes=d.size_bytes,
                created_at=d.created_at,
            )
            for d in docs
        ]


@router.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(document_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> DocumentOut:
    with tenant_session(principal.tenant_schema) as session:
        doc = session.get(Document, document_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="document not found")
        require_deal_role(session, doc.deal_id, principal.user_id, "viewer")
        return DocumentOut(
            id=doc.id,
            deal_id=doc.deal_id,
            filename=doc.filename,
            content_type=doc.content_type,
            size_bytes=doc.size_bytes,
            created_at=doc.created_at,
            download_url=presigned_download_url(doc.s3_key),
        )
