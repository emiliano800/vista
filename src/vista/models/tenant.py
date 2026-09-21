import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


class TenantBase(DeclarativeBase):
    """Tables created inside each tenant's schema (resolved via search_path)."""


class Deal(TenantBase):
    __tablename__ = "deals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))  # platform.users.id
    # Portfolio company profile: industry, location, acquired, currency. Free-form
    # because it is descriptive context, never an input to a calculated figure.
    profile: Mapped[dict] = mapped_column(JSONB, default=dict)
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
    # Which portfolio company this agent works on; null for firm-wide agents.
    deal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deals.id"), nullable=True, index=True)
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
    # Dimensions the CFO views group by. company is the synthetic short name or the Deal name.
    company: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    division: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    recording_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recordings.id"), nullable=True)
    agent_key: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # vista.agents.keys.AGENT_KEYS
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


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
    company: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    agent_key: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)


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
    # Denormalised from the run so spend groups without a join.
    agent_key: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    company: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class EvalRun(TenantBase):
    """One scoring of a phase against synthetic_data/answer_key.json (tier 3)."""

    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phase: Mapped[str] = mapped_column(String(16))  # discover|execute|analyze
    agent_key: Mapped[str] = mapped_column(String(32), index=True)
    sector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    company: Mapped[str | None] = mapped_column(String(64), nullable=True)
    division: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    predictions: Mapped[int] = mapped_column(Integer, default=0)
    tp: Mapped[int] = mapped_column(Integer, default=0)
    fp: Mapped[int] = mapped_column(Integer, default=0)
    fn: Mapped[int] = mapped_column(Integer, default=0)
    trap_hits: Mapped[int] = mapped_column(Integer, default=0)
    precision: Mapped[float] = mapped_column(Float, default=0.0)
    recall: Mapped[float] = mapped_column(Float, default=0.0)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)  # matched / missed / unmatched ids
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


# ---- Canonical business state (PE portfolio workspace) ------------------------
# Business facts live here; agent interpretation lives in findings / opportunities.
# Every row records where it came from so any number on screen can be traced.

SOURCE_TYPES = ("synthetic_seed", "manual_entry", "csv_import", "xlsx_import", "agent_import", "api_sync")


class ProvenanceMixin:
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)  # platform.firm_companies.id
    data_source_type: Mapped[str] = mapped_column(String(32), default="manual_entry")
    source_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    import_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    synthetic_demo: Mapped[bool] = mapped_column(Boolean, default=False)

    @declared_attr
    def created_at(cls) -> Mapped[datetime]:
        return mapped_column(DateTime(timezone=True), server_default=func.now())

    @declared_attr
    def updated_at(cls) -> Mapped[datetime]:
        return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Customer(ProvenanceMixin, TenantBase):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    legal_name: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(255))
    source_customer_id: Mapped[str] = mapped_column(String(64), default="")
    contact_name: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    address_line_1: Mapped[str] = mapped_column(String(255), default="")
    address_line_2: Mapped[str] = mapped_column(String(255), default="")
    city: Mapped[str] = mapped_column(String(128), default="")
    state: Mapped[str] = mapped_column(String(32), default="")
    postal_code: Mapped[str] = mapped_column(String(32), default="")
    service_type: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|inactive


class Invoice(ProvenanceMixin, TenantBase):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    customer_name: Mapped[str] = mapped_column(String(255), default="")  # as written in the source
    source_invoice_number: Mapped[str] = mapped_column(String(64))
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    outstanding_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    status: Mapped[str] = mapped_column(String(16), default="open")  # draft|open|overdue|paid|void|disputed


class Vendor(ProvenanceMixin, TenantBase):
    __tablename__ = "vendors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    normalized_name: Mapped[str] = mapped_column(String(255), index=True)
    source_name: Mapped[str] = mapped_column(String(255))
    source_vendor_id: Mapped[str] = mapped_column(String(64), default="")
    category: Mapped[str] = mapped_column(String(128), default="")
    payment_terms: Mapped[str] = mapped_column(String(64), default="")
    contact_name: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")


class VendorPurchase(ProvenanceMixin, TenantBase):
    __tablename__ = "vendor_purchases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vendors.id"), nullable=True, index=True)
    purchase_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    sku: Mapped[str] = mapped_column(String(64), default="", index=True)
    item_description: Mapped[str] = mapped_column(Text, default="")
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=0)
    unit: Mapped[str] = mapped_column(String(32), default="")
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)


class Subscription(ProvenanceMixin, TenantBase):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_name: Mapped[str] = mapped_column(String(255), default="")
    product_name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(128), default="")
    monthly_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    annual_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    seat_count: Mapped[int] = mapped_column(Integer, default=0)
    renewal_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    contract_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    restrictions_notes: Mapped[str] = mapped_column(Text, default="")


class Policy(ProvenanceMixin, TenantBase):
    """Insurance policy placed for a customer (client). Premium and commission are
    the source system's figures; nothing here is derived."""

    __tablename__ = "policies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    customer_name: Mapped[str] = mapped_column(String(255), default="")
    source_policy_id: Mapped[str] = mapped_column(String(64), default="")
    source_customer_id: Mapped[str] = mapped_column(String(64), default="")
    policy_number: Mapped[str] = mapped_column(String(64), index=True)
    line_of_business: Mapped[str] = mapped_column(String(32), default="")
    line_description: Mapped[str] = mapped_column(String(128), default="")
    carrier_code: Mapped[str] = mapped_column(String(32), default="", index=True)
    carrier_name: Mapped[str] = mapped_column(String(255), default="")
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiration_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    term_months: Mapped[int] = mapped_column(Integer, default=12)
    annual_premium: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    commission_pct: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=0)
    expected_commission: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    billing_type: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(32), default="in_force")
    producer_id: Mapped[str] = mapped_column(String(64), default="")
    account_manager_id: Mapped[str] = mapped_column(String(64), default="")
    surplus_lines: Mapped[bool] = mapped_column(Boolean, default=False)
    new_or_renewal: Mapped[str] = mapped_column(String(16), default="")
    experience_mod: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    umbrella_limit: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)


class PurchaseOrder(ProvenanceMixin, TenantBase):
    __tablename__ = "purchase_orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vendors.id"), nullable=True, index=True)
    po_number: Mapped[str] = mapped_column(String(64), index=True)
    source_supplier_id: Mapped[str] = mapped_column(String(64), default="")
    supplier_name: Mapped[str] = mapped_column(String(255), default="")
    po_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    buyer_id: Mapped[str] = mapped_column(String(64), default="")
    payment_terms: Mapped[str] = mapped_column(String(64), default="")
    ship_via: Mapped[str] = mapped_column(String(128), default="")
    freight_terms: Mapped[str] = mapped_column(String(64), default="")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    status: Mapped[str] = mapped_column(String(32), default="open")
    approved_by: Mapped[str] = mapped_column(String(64), default="")
    sent_method: Mapped[str] = mapped_column(String(64), default="")


class PurchaseOrderLine(ProvenanceMixin, TenantBase):
    __tablename__ = "purchase_order_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase_orders.id"), nullable=True, index=True)
    po_number: Mapped[str] = mapped_column(String(64), index=True)
    line_number: Mapped[int] = mapped_column(Integer, default=0)
    item_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    manufacturer_part_number: Mapped[str] = mapped_column(String(64), default="", index=True)
    ordered_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=0)
    uom: Mapped[str] = mapped_column(String(16), default="")
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=0)
    extended_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    need_by_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    promised_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    received_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=0)
    status: Mapped[str] = mapped_column(String(32), default="open")
    gl_account: Mapped[str] = mapped_column(String(32), default="")


class InventoryBalance(ProvenanceMixin, TenantBase):
    """Point-in-time stock position of one item in one bin (as_of_date is the source snapshot date)."""

    __tablename__ = "inventory_balances"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[str] = mapped_column(String(64), index=True)
    warehouse: Mapped[str] = mapped_column(String(64), default="")
    bin_location: Mapped[str] = mapped_column(String(64), default="")
    on_hand_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=0)
    allocated_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=0)
    available_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=0)
    on_order_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=0)
    uom: Mapped[str] = mapped_column(String(16), default="")
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=0)
    extended_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    last_count_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_receipt_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class Task(TenantBase):
    """Human work item. `realized_value` is only meaningful when outcome == 'Implemented'."""

    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    ref: Mapped[str] = mapped_column(String(16), unique=True)  # T-101 (firm-wide sequence)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(64), default="Integration")
    source_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # opportunity|finding|subscription|agent_run|import
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    assignee: Mapped[str] = mapped_column(String(255), default="")
    priority: Mapped[str] = mapped_column(String(16), default="Medium")
    status: Mapped[str] = mapped_column(String(16), default="Open")  # Open|In progress|Blocked|Complete|Dismissed
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome_notes: Mapped[str] = mapped_column(Text, default="")
    realized_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), default="")
    synthetic_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---- Raw source layer + placeholder import pipeline ------------------------------

IMPORT_STATUSES = ("uploaded", "analyzing", "mapping_review", "validating", "ready_to_import", "importing", "completed", "failed")


class SourceFile(TenantBase):
    __tablename__ = "source_files"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    filename: Mapped[str] = mapped_column(String(512))
    storage_key: Mapped[str] = mapped_column(String(1024), default="")
    mime_type: Mapped[str] = mapped_column(String(128), default="")
    file_size: Mapped[int] = mapped_column(BigInteger, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String(16), default="stored")  # stored|failed|purged


class ImportJob(TenantBase):
    __tablename__ = "import_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    source_file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_files.id"))
    status: Mapped[str] = mapped_column(String(16), default="uploaded")
    dataset_type: Mapped[str] = mapped_column(String(32), default="other")  # customers|invoices|vendors|subscriptions|other
    detection_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    sheet_name: Mapped[str] = mapped_column(String(128), default="Sheet1")
    columns: Mapped[list] = mapped_column(JSONB, default=list)
    processor: Mapped[str] = mapped_column(String(32), default="demo")
    records_detected: Mapped[int] = mapped_column(Integer, default=0)
    records_imported: Mapped[int] = mapped_column(Integer, default=0)
    records_needing_review: Mapped[int] = mapped_column(Integer, default=0)
    records_rejected: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FieldMapping(TenantBase):
    __tablename__ = "field_mappings"
    __table_args__ = (UniqueConstraint("import_job_id", "source_column"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    import_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_jobs.id", ondelete="CASCADE"), index=True)
    source_column: Mapped[str] = mapped_column(String(255))
    example_value: Mapped[str] = mapped_column(Text, default="")
    target_entity: Mapped[str] = mapped_column(String(32))
    target_field: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="proposed")  # proposed|needs_review|approved|ignored
    reason: Mapped[str] = mapped_column(Text, default="")  # why this target was proposed (alias hit, model, ...)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ImportRecord(TenantBase):
    __tablename__ = "import_records"
    __table_args__ = (UniqueConstraint("import_job_id", "source_row"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    import_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_jobs.id", ondelete="CASCADE"), index=True)
    source_row: Mapped[int] = mapped_column(Integer)
    raw_record: Mapped[dict] = mapped_column(JSONB, default=dict)
    normalized_record: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="staged")  # staged|imported|merged|rejected
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    exception_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)  # canonical row written on approval


class ImportException(TenantBase):
    __tablename__ = "import_exceptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    import_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_jobs.id", ondelete="CASCADE"), index=True)
    import_record_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("import_records.id"), nullable=True)
    ref: Mapped[str] = mapped_column(String(16), default="")  # X-1 within the job
    exception_type: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")
    candidate_matches: Mapped[list] = mapped_column(JSONB, default=list)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)  # left/right values, rows, allowed actions
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|resolved
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RecordProvenance(TenantBase):
    """Where a canonical value came from. `field_name` NULL covers the whole row."""

    __tablename__ = "record_provenance"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    field_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_filename: Mapped[str] = mapped_column(String(512), default="")
    sheet_name: Mapped[str] = mapped_column(String(128), default="")
    row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_column: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_value: Mapped[dict] = mapped_column(JSONB, default=dict)
    normalized_value: Mapped[dict] = mapped_column(JSONB, default=dict)
    import_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    human_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---- Agent interpretation layer for the portfolio workspace -----------------------
# Workspace agents, their runs and findings are interpretations, never business facts.
# Display detail lives in `payload`; the indexed columns are what the API filters on.


class WorkspaceAgent(TenantBase):
    __tablename__ = "workspace_agents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    ref: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="Active")  # Active|Paused
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)  # represents, cases, review, findings, lastFailure, cost
    synthetic_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkspaceAgentRun(TenantBase):
    __tablename__ = "workspace_agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspace_agents.id", ondelete="CASCADE"), index=True)
    ref: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="Complete")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    needs_review: Mapped[int] = mapped_column(Integer, default=0)
    model_cost: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=0)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)  # goal, sources, events, output, evidence, corrections
    synthetic_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class WorkspaceFinding(TenantBase):
    __tablename__ = "workspace_findings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    ref: Mapped[str] = mapped_column(String(16), unique=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workspace_agents.id"), nullable=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workspace_agent_runs.id"), nullable=True)
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(16), default="Medium")
    status: Mapped[str] = mapped_column(String(16), default="Open")  # Open|Reviewed|Actioned|Dismissed
    found_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    synthetic_demo: Mapped[bool] = mapped_column(Boolean, default=False)
