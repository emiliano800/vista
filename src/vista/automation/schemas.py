import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
ToolName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class WorkflowLimits(InputModel):
    max_steps: int = Field(default=10, strict=True, ge=1, le=100)
    max_runtime_seconds: int = Field(default=300, strict=True, ge=1, le=3600)
    max_cost_usd: Decimal = Field(default=Decimal("1.00"), ge=0, le=100, max_digits=8, decimal_places=4)


class WorkflowDefinition(InputModel):
    goal: Text
    required_inputs: list[Name] = Field(min_length=1, max_length=30)
    allowed_tools: list[ToolName] = Field(min_length=1, max_length=30)
    success_criteria: list[Text] = Field(min_length=1, max_length=20)
    environment: Literal["sandbox"] = "sandbox"
    limits: WorkflowLimits = Field(default_factory=WorkflowLimits)

    @field_validator("required_inputs", "allowed_tools", "success_criteria")
    @classmethod
    def unique_values(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Duplicate entries are not allowed")
        return values


class WorkflowCreate(InputModel):
    name: Name
    definition: WorkflowDefinition


class VersionCreate(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    definition: WorkflowDefinition


class DecisionCreate(InputModel):
    decision: Literal["approved", "rejected"]
    reason: str = Field(default="", max_length=2000)


class DecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version_id: uuid.UUID
    definition_hash: str
    decision: Literal["approved", "rejected"]
    reason: str
    decided_by: uuid.UUID
    decided_at: datetime


class VersionOut(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    number: int
    definition: WorkflowDefinition
    definition_hash: str
    status: Literal["draft", "approved", "rejected"]
    decision: DecisionOut | None
    created_by: uuid.UUID
    created_at: datetime


class WorkflowOut(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    name: str
    latest_version: VersionOut
    created_by: uuid.UUID
    created_at: datetime


class EligibilityOut(BaseModel):
    version_id: uuid.UUID
    eligible: bool
    reasons: list[str]
    # True only when the approval gate passes AND every allowed tool has a harness that is
    # connected for this company right now (`computer_use.tools.availability`).
    execution_available: bool = False
    availability: dict | None = None
