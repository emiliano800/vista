import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class TenantBase(DeclarativeBase):
    """Tables created inside each tenant's schema (resolved via search_path)."""


class Deal(TenantBase):
    __tablename__ = "deals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))  # platform.users.id
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DealMembership(TenantBase):
    __tablename__ = "deal_memberships"
    __table_args__ = (UniqueConstraint("deal_id", "user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deals.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))  # platform.users.id
    role: Mapped[str] = mapped_column(String(16))  # owner|member|viewer


class Document(TenantBase):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deals.id"))
    filename: Mapped[str] = mapped_column(String(512))
    s3_key: Mapped[str] = mapped_column(String(1024))
    content_type: Mapped[str] = mapped_column(String(255), default="application/octet-stream")
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImportBatch(TenantBase):
    __tablename__ = "import_batches"
    __table_args__ = (UniqueConstraint("deal_id", "content_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deals.id"), index=True)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    content_hash: Mapped[str] = mapped_column(String(64))
    s3_key: Mapped[str] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(16), default="preview")
    as_of: Mapped[str] = mapped_column(String(10))
    tables: Mapped[list] = mapped_column(JSONB)
    analysis: Mapped[dict] = mapped_column(JSONB, default=dict)
    events: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Employee(TenantBase):
    __tablename__ = "employees"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role_title: Mapped[str] = mapped_column(String(255))  # all the agent needs to know
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmployeeAgent(TenantBase):
    __tablename__ = "employee_agents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|paused
    scopes: Mapped[list] = mapped_column(JSONB, default=list)  # e.g. ["documents", "email:ro"]
    schedule: Mapped[str] = mapped_column(String(16), default="daily")  # hourly|daily|weekly
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentRun(TenantBase):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))  # platform.jobs.id
    run_type: Mapped[str] = mapped_column(String(32), default="deal_analysis")  # deal_analysis|employee_discovery|company_summary
    deal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deals.id"), nullable=True)
    employee_agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employee_agents.id"), nullable=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|succeeded|failed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentRunEvent(TenantBase):
    __tablename__ = "agent_run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id"))
    seq: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64))  # step|tool_call|model_call|error|result
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Finding(TenantBase):
    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id"))
    employee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employee_agents.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))  # observed_fact|inefficiency|proposed_automation
    title: Mapped[str] = mapped_column(String(512))
    detail: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)  # source refs / assumptions
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|reviewed|dismissed|actioned
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CompanySummary(TenantBase):
    __tablename__ = "company_summaries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id"))
    content: Mapped[str] = mapped_column(Text)
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsageEvent(TenantBase):
    __tablename__ = "usage_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id"))
    model: Mapped[str] = mapped_column(String(128))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Recording(TenantBase):
    __tablename__ = "recordings"
    __table_args__ = (UniqueConstraint("deal_id", "uploaded_by", "source_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deals.id"), index=True)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    source_id: Mapped[str] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active_seconds: Mapped[int] = mapped_column(Integer)
    manifest: Mapped[dict] = mapped_column(JSONB)
    summary: Mapped[dict] = mapped_column(JSONB)
    s3_key: Mapped[str] = mapped_column(String(1024))
    content_hash: Mapped[str] = mapped_column(String(64))
    media: Mapped[dict] = mapped_column(JSONB, default=dict)  # name -> {key, content_type, size_bytes}
    sections: Mapped[dict] = mapped_column(JSONB, default=dict)  # section id -> employee edits {name, note}
    files: Mapped[list] = mapped_column(JSONB, default=list)  # documents on screen: markers, snapshot key, extraction state
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RecordingReviewItem(TenantBase):
    """One AI explanation of a stretch of a recording (a video section, or the
    whole session as item_id 'session') and what the employee decided about it."""

    __tablename__ = "recording_review_items"
    __table_args__ = (UniqueConstraint("recording_id", "item_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recording_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recordings.id"), index=True)
    item_id: Mapped[str] = mapped_column(String(64))
    section: Mapped[dict] = mapped_column(JSONB, default=dict)  # name, app, title, start, end, seconds, counts
    prompt: Mapped[str] = mapped_column(Text)  # what the recorder described to the model (already redacted)
    # pending|proposed|unsure|approved|fixed|explained|failed
    status: Mapped[str] = mapped_column(String(16), default="pending")
    label: Mapped[str] = mapped_column(Text, default="")
    explanation: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0)
    unclear: Mapped[list] = mapped_column(JSONB, default=list)
    questions: Mapped[list] = mapped_column(JSONB, default=list)
    threshold: Mapped[float] = mapped_column(Float)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    explained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)  # approve|fix|explain
    final_label: Mapped[str] = mapped_column(Text, default="")
    final_note: Mapped[str] = mapped_column(Text, default="")
    answers: Mapped[list] = mapped_column(JSONB, default=list)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
