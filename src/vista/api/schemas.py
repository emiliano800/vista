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
    deal_id: uuid.UUID | None = None
    scopes: list[str] = []
    schedule: str = "daily"  # hourly|daily|weekly


class AgentOut(BaseModel):
    id: uuid.UUID
    employee_id: uuid.UUID
    deal_id: uuid.UUID | None = None
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
    company: str | None = None
    agent_key: str | None = None


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
    started_at: datetime | None = None
    company: str | None = None
    division: str | None = None
    sector: str | None = None
    agent_key: str | None = None
    recording_id: uuid.UUID | None = None
    error: str | None = None
    events: list[RunEventOut] = []


class UsageGroupOut(BaseModel):
    key: dict[str, str | None]  # one entry per group_by dimension
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    runs: int


class UsageOut(BaseModel):
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: Decimal
    runs: int
    groups: list[UsageGroupOut] = []


class PortfolioCompanyOut(BaseModel):
    id: uuid.UUID
    name: str
    profile: dict = {}
    created_at: datetime
    as_of: str | None = None
    records: int = 0
    findings_open: int = 0
    findings_total: int = 0
    exposure: Decimal = Decimal(0)
    imports: int = 0
    agents_active: int = 0
    last_run_at: datetime | None = None


class TaskCreate(BaseModel):
    deal_id: uuid.UUID
    title: str
    description: str = ""
    category: str = "Integration"
    source_type: str | None = None
    source_id: str | None = None
    assignee: str = ""
    priority: str = "Medium"
    due_date: str | None = None


class TaskPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    assignee: str | None = None
    priority: str | None = None
    due_date: str | None = None
    status: str | None = None
    outcome: str | None = None
    outcome_notes: str | None = None
    realized_result: Decimal | None = None


class TaskOut(BaseModel):
    id: uuid.UUID
    deal_id: uuid.UUID
    title: str
    description: str
    category: str
    source_type: str | None
    source_id: str | None
    assignee: str
    priority: str
    due_date: str | None
    status: str
    created_by: str
    created_at: datetime
    completed_at: datetime | None
    outcome: str | None
    outcome_notes: str
    realized_result: Decimal | None


class OpportunityPatch(BaseModel):
    status: str
    realized_value: Decimal | None = None


class OpportunityOut(BaseModel):
    id: uuid.UUID
    title: str
    category: str
    deal_ids: list[uuid.UUID]
    confidence: float
    potential_value: Decimal
    status: str
    found_at: datetime
    fact: str
    evidence: list
    calculation: list
    benefit: str
    assumptions: list
    next_action: str
    realized_value: Decimal | None


class ActivityOut(BaseModel):
    id: uuid.UUID
    deal_id: uuid.UUID | None
    kind: str
    summary: str
    actor: str
    ref: dict
    at: datetime


class PortfolioCompanyDetail(BaseModel):
    """One company's records, reshaped from its newest committed import batch.

    `unsourced` names the record kinds the portfolio UI can show but the
    ingestion schema has no source for, so the workspace can say "no source"
    instead of rendering a zero that looks like a measurement.
    """

    id: uuid.UUID
    name: str
    profile: dict = {}
    as_of: str | None = None
    batch_id: uuid.UUID | None = None
    customers: list = []
    invoices: list = []
    vendors: list = []
    policies: list = []
    commissions: list = []
    subscriptions: list = []
    purchases: list = []
    unsourced: list[str] = []
    exceptions: list = []
    analysis: dict = {}


class AnalysisRunOut(BaseModel):
    """What a portfolio analysis pass found, in records rather than prose."""

    companies: int
    skus_compared: int
    created: list[OpportunityOut]
    updated: list[OpportunityOut]
