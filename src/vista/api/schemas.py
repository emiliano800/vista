import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, EmailStr


class TenantCreate(BaseModel):
    name: str
    owner_email: EmailStr


class TenantCreated(BaseModel):
    tenant_id: uuid.UUID
    name: str
    owner_user_id: uuid.UUID
    owner_email: str
    api_token: str  # returned exactly once at provisioning


class DealCreate(BaseModel):
    name: str


class DealOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime


class DocumentCreate(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"
    size_bytes: int | None = None


class DocumentCreated(BaseModel):
    id: uuid.UUID
    filename: str
    upload_url: str


class DocumentOut(BaseModel):
    id: uuid.UUID
    deal_id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int | None
    created_at: datetime
    download_url: str | None = None


class RunCreate(BaseModel):
    deal_id: uuid.UUID
    document_id: uuid.UUID | None = None
    idempotency_key: str | None = None


class RunEventOut(BaseModel):
    seq: int
    event_type: str
    data: dict
    created_at: datetime


class RunOut(BaseModel):
    id: uuid.UUID
    deal_id: uuid.UUID
    document_id: uuid.UUID | None
    status: str
    created_at: datetime
    finished_at: datetime | None
    events: list[RunEventOut] = []


class UsageOut(BaseModel):
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: Decimal
    runs: int
