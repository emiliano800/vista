"""Execute phase — division_executor: one division's tables in, findings + tasks out.

The model may only request tools; apply() enforces the agent's scopes and
returns undo payloads. Anything ambiguous becomes an exception for a human."""

import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from vista.agents.llm import Prompt, strip_fences
from vista.agents.synthetic import Company, Table

Tool = Literal["create_finding", "create_task", "normalise_records", "raise_exception"]

# tool -> scope needed (raise_exception is always allowed); normalise_records also needs an approved proposal
TOOL_SCOPES: dict[str, str | None] = {
    "create_finding": "findings:write",
    "create_task": "tasks:write",
    "normalise_records": "records:write",
    "raise_exception": None,
}
FINDING_KINDS = {
    "certificate",
    "commission",
    "compliance",
    "three_way_match",
    "inventory_integrity",
    "receivables",
    "revenue_leakage",
    "data_quality",
    "order_entry",
    "quality",
    "engineering_change",
    "overpayment",
}
MAX_TABLE_ROWS = 60


class PermissionDenied(Exception):
    pass


class ToolCall(BaseModel):
    tool: Tool
    args: dict = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list, max_length=10)  # table refs


class ExecuteOutput(BaseModel):
    actions: list[ToolCall] = Field(default_factory=list, max_length=30)


SYSTEM = (
    "You are Vista's Division Executor for one operational division of a small company. You see the division's "
    "tables (trimmed) and the approved configuration proposals. Find concrete, evidence-backed issues and act "
    "through tools only: create_finding {kind, title, detail} (kind in: " + ", ".join(sorted(FINDING_KINDS)) + "), "
    "create_task {title, detail, assignee_role, due_in_days}, normalise_records {proposal_id, table, changes} "
    "(only with an approved proposal), raise_exception {reason} when evidence is ambiguous. Every action lists the "
    "table refs it rests on. Do not invent records. "
    'Respond with JSON only: {"actions": [{"tool": str, "args": object, "evidence": [str]}]}.'
)


def _trim(table: Table) -> dict:
    return {"ref": table.ref, "columns": table.columns, "rows": table.rows[:MAX_TABLE_ROWS], "total_rows": len(table.rows)}


def prepare(company: Company, division: str, tables: list[Table], approved_proposals: list[dict]) -> Prompt:
    user = json.dumps(
        {
            "company": {"short": company.short, "sector": company.sector, "data_quality_tier": company.tier},
            "division": division,
            "approved_proposals": approved_proposals,
            "tables": [_trim(t) for t in tables],
        },
        default=str,
    )
    return Prompt(system=SYSTEM, user=user, max_tokens=2000)


def parse(text: str) -> ExecuteOutput:
    try:
        return ExecuteOutput.model_validate_json(strip_fences(text))
    except ValidationError:
        return ExecuteOutput()


def apply(output: ExecuteOutput, scopes: set[str], approved_proposal_ids: set[str], known_refs: set[str]) -> list[dict]:
    """actions rows with undo payloads. Raises PermissionDenied on the first out-of-scope tool;
    unknown evidence refs and unapproved normalisations become raise_exception rows."""
    rows = []
    for call in output.actions:
        needed = TOOL_SCOPES[call.tool]
        if needed is not None and needed not in scopes:
            raise PermissionDenied(f"{call.tool} needs scope {needed}")
        evidence = [e for e in call.evidence if e in known_refs]
        if not evidence:
            rows.append(_exception(call, "no known evidence ref"))
            continue
        if call.tool == "normalise_records" and str(call.args.get("proposal_id")) not in approved_proposal_ids:
            rows.append(_exception(call, "normalise_records without an approved proposal"))
            continue
        if call.tool == "raise_exception":
            rows.append({"tool": call.tool, "args": call.args, "evidence_refs": evidence, "status": "open", "undo": None})
            continue
        if call.tool == "create_finding" and call.args.get("kind") not in FINDING_KINDS:
            call.args["kind"] = "data_quality"
        rows.append(
            {
                "tool": call.tool,
                "args": call.args,
                "evidence_refs": evidence,
                "status": "done",
                "undo": {"tool": f"undo_{call.tool}", "args": call.args},
            }
        )
    return rows


def _exception(call: ToolCall, reason: str) -> dict:
    return {
        "tool": "raise_exception",
        "args": {"reason": reason, "requested": call.model_dump()},
        "evidence_refs": call.evidence,
        "status": "open",
        "undo": None,
    }
