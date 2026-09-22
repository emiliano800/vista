import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from vista.automation.schemas import Name, WorkflowLimits


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---- the inputs a run may type from or read: declared up front, never model-generated ----------


class DocumentInput(InputModel):
    kind: Literal["document"]
    submission_id: uuid.UUID
    artifact_id: str = Field(min_length=1, max_length=128)


class RecordsInput(InputModel):
    kind: Literal["records"]
    table: Literal["customers", "invoices", "vendors", "purchase_orders", "subscriptions", "policies"]
    ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class ValueInput(InputModel):
    kind: Literal["value"]
    value: str = Field(min_length=1, max_length=512)


InputBinding = Annotated[DocumentInput | RecordsInput | ValueInput, Field(discriminator="kind")]


class RunStart(InputModel):
    mode: Literal["dry_run", "sandbox"] = "sandbox"
    inputs: dict[Name, InputBinding] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=255)


class DecisionIn(InputModel):
    step_id: uuid.UUID
    decision: Literal["approve", "deny"]
    reason: str = Field(default="", max_length=2000)


class StopIn(InputModel):
    reason: str = Field(default="", max_length=2000)


class WorkflowRunOut(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    workflow_name: str
    version_id: uuid.UUID
    version_number: int
    definition_hash: str
    agent_run_id: uuid.UUID
    mode: str
    status: str
    steps_used: int
    limits: WorkflowLimits
    cost_usd: Decimal
    pending: dict | None
    harness: dict | None
    outcome: dict | None
    error: str | None
    requested_by: uuid.UUID
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


# ---- the recorder's side of the protocol ----------------------------------------------------------


class ConsentIn(InputModel):
    version: str = Field(min_length=1, max_length=64)
    accepted_at: datetime
    screenshots: bool = False


class ClaimIn(InputModel):
    device_id: str = Field(min_length=1, max_length=128)
    consent: ConsentIn
    capabilities: dict = Field(default_factory=dict)


class StepResultIn(InputModel):
    device_id: str = Field(min_length=1, max_length=128)
    lease_token: str = Field(min_length=1, max_length=64)
    ok: bool
    description: str = Field(default="", max_length=500)
    observation: dict | None = None
    result: dict | None = None
    evidence: dict | None = None
    error: dict | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class SessionStopIn(InputModel):
    device_id: str = Field(min_length=1, max_length=128)
    lease_token: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=200)
