"""Propose phase — config_proposer: facts in, reviewable proposals out.

Each proposal is a diff a human can approve, edit or reject; nothing here writes
to records. Proposals must cite the facts (by index) they rest on."""

import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from vista.agents.llm import Prompt, strip_fences
from vista.agents.synthetic import Company

ProposalKind = Literal["column_mapping", "dedupe_merge", "rule", "workflow_change"]


class Proposal(BaseModel):
    kind: ProposalKind
    title: str = Field(max_length=256)
    diff: dict = Field(default_factory=dict)  # column_mapping: {"from": {...}, "to": {...}} etc.
    evidence: list[int] = Field(default_factory=list, max_length=20)  # indexes into the facts list given
    rationale: str = Field(default="", max_length=2000)


class ProposeOutput(BaseModel):
    proposals: list[Proposal] = Field(default_factory=list, max_length=20)


SYSTEM = (
    "You are Vista's Config Proposer. Given observed facts about one company's exported tables (each fact has "
    "an index) and the company profile, propose configuration changes Vista should apply, as reviewable diffs: "
    "column_mapping (rename/swap columns to canonical names), dedupe_merge (records that are the same entity), "
    "rule (validation or approval rule), workflow_change (a step to add/remove in the documented procedure). "
    "Every proposal must list the fact indexes it relies on; do not propose anything without evidence. "
    'Respond with JSON only: {"proposals": [{"kind": str, "title": str, "diff": object, "evidence": [int], '
    '"rationale": str}]}.'
)


def prepare(company: Company, facts: list[dict], procedures_excerpt: str = "") -> Prompt:
    indexed = [
        {
            "i": i,
            **{k: f[k] for k in ("subject", "predicate", "value", "confidence") if k in f},
            "file": f.get("source_ref", {}).get("file"),
        }
        for i, f in enumerate(facts)
    ]
    user = json.dumps(
        {
            "company": {"short": company.short, "sector": company.sector, "data_quality_tier": company.tier},
            "operating_procedures_excerpt": procedures_excerpt[:4000],
            "facts": indexed,
        }
    )
    return Prompt(system=SYSTEM, user=user, max_tokens=1500)


def parse(text: str) -> ProposeOutput:
    try:
        return ProposeOutput.model_validate_json(strip_fences(text))
    except ValidationError:
        return ProposeOutput()


def apply(output: ProposeOutput, facts: list[dict]) -> list[dict]:
    """proposals rows, status open; drops proposals whose evidence indexes are invalid or empty."""
    rows = []
    for p in output.proposals:
        evidence = [facts[i] for i in p.evidence if 0 <= i < len(facts)]
        if not evidence:
            continue
        rows.append(
            {
                "kind": p.kind,
                "title": p.title,
                "diff": p.diff,
                "rationale": p.rationale,
                "evidence_refs": [e.get("source_ref") for e in evidence],
                "status": "open",
            }
        )
    return rows
