import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    StringConstraints,
    field_validator,
    model_serializer,
    model_validator,
)

from taskmining.state import ACTION_CLASSES, APP_ROLES, POLICIES, SIGNATURE_TOKEN, edge_id, state_key

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
ToolName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Key = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{16}$")]
SignatureToken = Annotated[str, StringConstraints(pattern=SIGNATURE_TOKEN.pattern)]
AppRole = Literal["accounting", "crm", "spreadsheet", "pdf", "email", "browser", "documents", "chat", "workspace", "other"]
ActionClass = Literal["navigate", "click", "type_value", "press", "read", "extract", "submit", "wait", "http_get", "create_task"]
EdgePolicy = Literal["auto", "confirm", "always_ask"]
assert set(AppRole.__args__) == set(APP_ROLES) and set(ActionClass.__args__) == ACTION_CLASSES and EdgePolicy.__args__ == POLICIES


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class WorkflowLimits(InputModel):
    max_steps: int = Field(default=10, strict=True, ge=1, le=100)
    max_runtime_seconds: int = Field(default=300, strict=True, ge=1, le=3600)
    max_cost_usd: Decimal = Field(default=Decimal("1.00"), ge=0, le=100, max_digits=8, decimal_places=4)


# ---- the plan graph: states the recordings passed through and the observed moves between them ----
# Compiled in code on the employee's device (src/recorder/src/plan.js) from one or more recordings,
# later grown by verified runs (taskmining.state.merge_graphs). Names only — a signature token says
# *which* field holds a value or *which* fact is known, never what it is.


class GraphNode(InputModel):
    key: Key
    app_role: AppRole
    activity: Name
    signature: list[SignatureToken] = Field(default_factory=list, max_length=40)
    terminal: bool = False

    @model_validator(mode="after")
    def keyed(self):
        if self.key != state_key(self.app_role, self.activity, self.signature):
            raise ValueError("Node key does not match its state")
        if len(set(self.signature)) != len(self.signature):
            raise ValueError("Duplicate signature tokens")
        return self


class EdgeStats(InputModel):
    support: int = Field(default=0, strict=True, ge=0)
    recorded: int = Field(default=0, strict=True, ge=0)
    executed: int = Field(default=0, strict=True, ge=0)
    verified_ok: int = Field(default=0, strict=True, ge=0)
    approved: int = Field(default=0, strict=True, ge=0)
    denied: int = Field(default=0, strict=True, ge=0)
    effect_missing: int = Field(default=0, strict=True, ge=0)


class Provenance(InputModel):
    source: Literal["recording", "run"]
    id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
    event_ids: list[Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_:-]{1,64}$")]] = Field(default_factory=list, max_length=50)


class GraphEdge(InputModel):
    id: Key
    frm: Key
    to: Key
    action_class: ActionClass
    control: Name | None = None
    slot: Name | None = None
    produces: list[Name] = Field(default_factory=list, max_length=20)
    effect: list[SignatureToken] = Field(default_factory=list, max_length=20)
    stats: EdgeStats = Field(default_factory=EdgeStats)
    provenance: list[Provenance] = Field(min_length=1, max_length=50)
    policy: EdgePolicy = "confirm"
    anchor_ref: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_:-]{1,160}$")] | None = None

    @model_validator(mode="after")
    def keyed(self):
        if self.id != edge_id(self.frm, self.to, self.action_class, self.control, self.slot):
            raise ValueError("Edge id does not match its endpoints and action")
        if self.action_class == "submit" and self.policy != "always_ask":
            raise ValueError("A submit edge always asks a person")
        return self


class PlanGraph(InputModel):
    start: list[Key] = Field(min_length=1, max_length=20)
    nodes: list[GraphNode] = Field(min_length=1, max_length=200)
    edges: list[GraphEdge] = Field(max_length=600)
    trajectories: int = Field(strict=True, ge=1)
    truncated: bool = False
    compiled_by: Literal["recorder-plan/1"] = "recorder-plan/1"

    @model_validator(mode="after")
    def connected(self):
        keys = {n.key for n in self.nodes}
        if len(keys) != len(self.nodes):
            raise ValueError("Duplicate node keys")
        if len({e.id for e in self.edges}) != len(self.edges):
            raise ValueError("Duplicate edge ids")
        if not set(self.start) <= keys or any(e.frm not in keys or e.to not in keys for e in self.edges):
            raise ValueError("Edges and start states must reference nodes of this graph")
        return self

    @property
    def slots(self) -> set[str]:
        return {e.slot for e in self.edges if e.slot}

    @property
    def produced(self) -> set[str]:
        return {name for e in self.edges for name in e.produces}


class WorkflowDefinition(InputModel):
    goal: Text
    required_inputs: list[Name] = Field(min_length=1, max_length=30)
    allowed_tools: list[ToolName] = Field(min_length=1, max_length=30)
    success_criteria: list[Text] = Field(min_length=1, max_length=20)
    environment: Literal["sandbox"] = "sandbox"
    limits: WorkflowLimits = Field(default_factory=WorkflowLimits)
    graph: PlanGraph | None = None

    @field_validator("required_inputs", "allowed_tools", "success_criteria")
    @classmethod
    def unique_values(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Duplicate entries are not allowed")
        return values

    @model_serializer(mode="wrap")
    def without_absent_graph(self, handler: SerializerFunctionWrapHandler) -> dict:
        # Definitions written before graphs existed keep their shape (and their hash).
        data = handler(self)
        if data.get("graph") is None:
            data.pop("graph", None)
        return data

    @model_validator(mode="after")
    def slots_declared(self):
        # A `type_value` edge may only draw on a declared input or a fact an earlier edge produces.
        if self.graph is not None:
            unknown = self.graph.slots - set(self.required_inputs) - self.graph.produced
            if unknown:
                raise ValueError(f"Graph slots are not declared inputs or produced facts: {sorted(unknown)}")
        return self


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
