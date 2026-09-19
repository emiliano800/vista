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


class EmployeeCreate(BaseModel):
    name: str
    role_title: str
    email: EmailStr | None = None


class EmployeeOut(BaseModel):
    id: uuid.UUID
    name: str
    role_title: str
    email: str | None
    created_at: datetime


class AgentCreate(BaseModel):
    employee_id: uuid.UUID
    scopes: list[str] = []
    schedule: str = "daily"  # hourly|daily|weekly


class AgentOut(BaseModel):
    id: uuid.UUID
    employee_id: uuid.UUID
    employee_name: str
    role_title: str
    status: str
    scopes: list[str]
    schedule: str
    last_run_at: datetime | None
    created_at: datetime


class AgentPatch(BaseModel):
    status: str | None = None  # active|paused
    scopes: list[str] | None = None
    schedule: str | None = None


class FindingOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    employee_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    kind: str
    title: str
    detail: str
    evidence: dict
    status: str
    created_at: datetime


class FindingPatch(BaseModel):
    status: str  # open|reviewed|dismissed|actioned


class SummaryOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    content: str
    stats: dict
    created_at: datetime


class RunEventOut(BaseModel):
    seq: int
    event_type: str
    data: dict
    created_at: datetime


class RunOut(BaseModel):
    id: uuid.UUID
    run_type: str = "deal_analysis"
    deal_id: uuid.UUID | None
    employee_agent_id: uuid.UUID | None = None
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
