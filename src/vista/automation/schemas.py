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

from taskmining.state import (
    ACTION_CLASSES,
    APP_ROLES,
    CRITERIA_TYPES,
    IRREVERSIBILITY,
    POLICIES,
    SIGNATURE_TOKEN,
    SLOT_METHODS,
    edge_id,
    node_key,
)

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
ToolName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Key = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{16}$")]
SignatureToken = Annotated[str, StringConstraints(pattern=SIGNATURE_TOKEN.pattern)]
AppRole = Literal["accounting", "crm", "spreadsheet", "pdf", "email", "browser", "documents", "chat", "workspace", "other"]
ActionClass = Literal["navigate", "click", "type_value", "press", "read", "extract", "submit", "wait", "http_get", "create_task"]
EdgePolicy = Literal["auto", "confirm", "always_ask"]
Irreversibility = Literal["navigational", "mutating", "committing"]
SlotMethod = Literal["transfer", "declared", "descriptor", "storyboard"]
CriterionType = Literal["read_back", "present", "graded"]
assert set(AppRole.__args__) == set(APP_ROLES) and set(ActionClass.__args__) == ACTION_CLASSES and EdgePolicy.__args__ == POLICIES
assert Irreversibility.__args__ == IRREVERSIBILITY and SlotMethod.__args__ == SLOT_METHODS and CriterionType.__args__ == CRITERIA_TYPES


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


class NodeContext(InputModel):
    """L1: structure around the frame — context for tie-breaks and `effect_seen`, never identity."""

    landmarks: list[Name] = Field(default_factory=list, max_length=20)
    modal: bool = False
    primary: dict | None = None
    controls: list[Name] = Field(default_factory=list, max_length=40)


class GraphNode(InputModel):
    key: Key
    app_role: AppRole
    activity: Name
    signature: list[SignatureToken] = Field(default_factory=list, max_length=40)
    # v3: the L0 set is the identity; `signature` mirrors it for consumers of older graphs.
    l0: list[SignatureToken] | None = Field(default=None, max_length=40)
    l1: NodeContext | None = None
    terminal: bool = False

    @model_serializer(mode="wrap")
    def _without_absent_v3_fields(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        for k in ("l0", "l1"):
            if data.get(k) is None:
                data.pop(k, None)
        return data

    @model_validator(mode="after")
    def keyed(self):
        if self.key != node_key(self.model_dump(exclude_none=True)):
            raise ValueError("Node key does not match its state")
        if len(set(self.signature)) != len(self.signature):
            raise ValueError("Duplicate signature tokens")
        if self.l0 is not None and sorted(set(self.l0)) != sorted(self.signature):
            raise ValueError("A v3 node's signature must mirror its L0 set")
        return self


class ControlDescriptor(InputModel):
    """(role, normalised name, landmark, position class) — never a raw string, selector or coordinate."""

    role: Name
    name: Name | None = None
    landmark: Name | None = None
    position: Name | None = None
    aliases: list[Name] = Field(default_factory=list, max_length=10)


class SlotAlignment(InputModel):
    slot: Name
    method: SlotMethod
    controls: list[Name] = Field(default_factory=list, max_length=20)
    single_recording: bool = False


class Criterion(InputModel):
    type: CriterionType
    slot: Name
    source: Literal["draft", "employee", "fde"] = "draft"
    read_back_via: Key | None = None
    read_back_delay: int | None = Field(default=None, ge=0, le=600)
    threshold: float | None = Field(default=None, ge=0, le=1)


class GoalFrame(InputModel):
    node: Key
    l0: list[SignatureToken] = Field(default_factory=list, max_length=40)
    criteria: list[Criterion] = Field(default_factory=list, max_length=20)


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
    descriptor: ControlDescriptor | None = None
    irreversibility: Irreversibility | None = None
    commit: Name | None = None

    @model_serializer(mode="wrap")
    def _without_absent_v3_fields(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        for k in ("descriptor", "irreversibility", "commit"):
            if data.get(k) is None:
                data.pop(k, None)
        return data

    @model_validator(mode="after")
    def keyed(self):
        if self.id != edge_id(self.frm, self.to, self.action_class, self.control, self.slot):
            raise ValueError("Edge id does not match its endpoints and action")
        if self.action_class == "submit" and self.policy != "always_ask":
            raise ValueError("A submit edge always asks a person")
        if self.action_class == "submit" and self.irreversibility not in (None, "committing"):
            raise ValueError("A submit edge is committing")
        if self.irreversibility == "committing" and self.policy != "always_ask":
            raise ValueError("A committing edge always asks a person")
        return self


VocabWord = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class GraphVocabulary(InputModel):
    """The words a graph may say as themselves: control names recurring across screens of the
    recording (`taskmining.normalise.Vocabulary`). Everything else in a name is a class token."""

    method: Literal["recurring-ax-names/1"] = "recurring-ax-names/1"
    min_screens: int = Field(default=2, strict=True, ge=1)
    screens: int = Field(default=0, strict=True, ge=0)
    size: int = Field(default=0, strict=True, ge=0)
    words: list[VocabWord] = Field(default_factory=list, max_length=2000)
    extra: list[VocabWord] = Field(default_factory=list, max_length=500)


class PlanGraph(InputModel):
    start: list[Key] = Field(min_length=1, max_length=20)
    nodes: list[GraphNode] = Field(min_length=1, max_length=200)
    edges: list[GraphEdge] = Field(max_length=600)
    trajectories: int = Field(strict=True, ge=1)
    truncated: bool = False
    compiled_by: Literal["recorder-plan/1", "recorder-plan/2", "recorder-plan/3"] = "recorder-plan/1"
    vocabulary: GraphVocabulary | None = None
    slot_table: list[SlotAlignment] | None = Field(default=None, max_length=100)
    goal: GoalFrame | None = None

    @model_serializer(mode="wrap")
    def _without_absent_optional(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        for k in ("vocabulary", "slot_table", "goal"):
            if data.get(k) is None:
                data.pop(k, None)
        return data

    @model_validator(mode="after")
    def connected(self):
        keys = {n.key for n in self.nodes}
        if len(keys) != len(self.nodes):
            raise ValueError("Duplicate node keys")
        if len({e.id for e in self.edges}) != len(self.edges):
            raise ValueError("Duplicate edge ids")
        if self.compiled_by == "recorder-plan/3" and any(n.l0 is None for n in self.nodes):
            raise ValueError("A v3 graph's nodes carry L0 sets")
        if self.goal is not None and self.goal.node not in keys:
            raise ValueError("The goal frame must be a node of this graph")
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
