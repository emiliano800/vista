"""What an admin sees of a workflow version's task graph, and how a run's statistics become
the next draft.

Runs never touch an approved version: each terminal run of a version is folded (as the
`graph_delta` `computer_use.graph.merge_run` derives from its checkpoint) into a *derived*
draft graph here, on request. The admin reads the approved structure, the per-edge policy and
statistics, every run's located node / chosen edge, the code-computed promotion proposals and
the automatic demotions — and then, explicitly, asks for a new draft version that carries
them. That draft goes through the ordinary approval gate; statistics alone never change what
the agent may do.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from taskmining import leakage, tiers
from taskmining.normalise import Vocabulary
from taskmining.state import edge_id, empty_stats, merge_graphs, promotable, stricter
from vista.automation import service
from vista.automation.schemas import EdgePolicy, InputModel, Key, PlanGraph, Tier, VersionCreate, VersionOut, WorkflowDefinition
from vista.computer_use.graph import merge_run
from vista.computer_use.planner import PlannerState
from vista.models.tenant import Workflow, WorkflowApproval, WorkflowRun, WorkflowVersion

TERMINAL = ("succeeded", "failed", "stopped")
DEMOTION_REASONS = ("denied", "effect_missing")
# The policy a pinned tier implies at run time (v3 edges carry both; the tier is the source of truth).
TIER_POLICY: dict[str, str] = {"shadow": "confirm", "ask": "always_ask", "confirm": "confirm", "unattended": "auto"}
UNDER_SEGMENTED_OUT = 6  # mirrors recorder/src/plan.js: more out-edges than this and a state is under-segmented


class RunStep(BaseModel):
    step_id: str
    edge: str
    seq: int | None = None
    effect_seen: bool | None = None
    executed: bool
    decision: Literal["approve", "deny"] | None = None


class GraphRun(BaseModel):
    run_id: uuid.UUID
    status: str
    mode: str
    verified: bool | None
    finished_at: str | None
    steps: list[RunStep]


class EdgeChange(BaseModel):
    edge_id: Key
    kind: Literal["stats", "policy", "tier"]
    before: dict | str
    after: dict | str
    reason: str = ""


class Proposal(BaseModel):
    edge_id: Key
    from_policy: EdgePolicy
    to_policy: EdgePolicy
    reason: str
    from_tier: Tier | None = None
    to_tier: Tier | None = None
    needs_fde: bool = False  # promotion past `ask` is an FDE click ...
    cooled: bool = True  # ... after the cooling period
    ceiling: Tier | None = None


class ShadowDisagreement(BaseModel):
    """The recorder proposed `edge` from `frm`; the employee went to `acted` instead. A structural
    delta the FDE can accept (a new edge `frm → acted`) — never applied on its own."""

    run_id: uuid.UUID
    edge_id: Key
    frm: Key
    acted: Key | None


class ShadowEdgeReport(BaseModel):
    edge_id: Key
    proposed: int
    agreed: int
    agreement: float
    disagreements: list[ShadowDisagreement]


class ShadowReport(BaseModel):
    runs: int
    proposed: int
    agreed: int
    agreement: float | None
    edges: list[ShadowEdgeReport]


class CompileSlot(BaseModel):
    slot: str
    method: str
    controls: list[str]
    single_recording: bool


class CompileCriterion(BaseModel):
    type: str
    slot: str
    source: str
    read_back_via: str | None = None
    covers: list[Key] = Field(default_factory=list)  # write edges this read-back covers


class CompileReport(BaseModel):
    """What the FDE reads of a compiled graph in the cloud. Derived from the graph alone: the
    device kept the recording, so the leakage result here is the cloud's half of the check
    (`recorder_uploads.plan_leakage`) and held-out locate/coverage arrive from the eval harness,
    never from this view."""

    compiled_by: str
    recordings: list[str]  # provenance ids of source recordings
    runs: int  # provenance ids of runs that added statistics
    states: int
    moves: int
    irreversibility: dict[str, int]
    slots: list[CompileSlot]
    aliases: dict[Key, list[str]]  # per edge: descriptor aliases the compiler kept
    criteria: list[CompileCriterion]
    uncovered_writes: list[Key]  # write edges no read-back covers: their ceiling is `confirm`
    vocabulary_size: int
    under_segmented: int
    leakage_ok: bool
    leakage_strings_checked: int
    held_out: dict | None = None


class GraphReviewOut(BaseModel):
    version_id: uuid.UUID
    version_number: int
    status: Literal["draft", "approved", "rejected"]
    graph: PlanGraph | None
    runs: list[GraphRun]
    draft: PlanGraph | None
    changes: list[EdgeChange]
    proposals: list[Proposal]
    shadow: ShadowReport
    compile: CompileReport | None = None
    previous_version_number: int | None = None
    against_previous: list[EdgeChange]
    can_draft: bool
    fde: bool = False  # the requester holds the FDE scope (server-derived; the client never claims it)


class DisagreementAccept(InputModel):
    edge_id: Key
    acted: Key


class GraphDraftCreate(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    promote: list[Key] = Field(default_factory=list, max_length=600)
    # Shadow disagreements the FDE accepts as structure: a new edge `frm → acted` with the
    # proposed edge's move, at `shadow`/`always_ask`. FDE-only.
    accept: list[DisagreementAccept] = Field(default_factory=list, max_length=100)


def _runs(session: Session, version: WorkflowVersion) -> list[WorkflowRun]:
    stmt = (
        select(WorkflowRun)
        .where(WorkflowRun.workflow_version_id == version.id, WorkflowRun.status.in_(TERMINAL))
        .order_by(WorkflowRun.created_at)
    )
    return list(session.scalars(stmt))


def _run_view(run: WorkflowRun, state: PlannerState) -> GraphRun:
    verified = (run.outcome or {}).get("verified")
    decisions = {k: v.get("decision") for k, v in state.decisions.items()}
    steps = [
        RunStep(
            step_id=t["step_id"],
            edge=t["edge"],
            seq=t.get("seq"),
            effect_seen=t.get("effect_seen"),
            executed=state.executed(t["step_id"]),
            decision=decisions.get(t["step_id"]),
        )
        for t in state.trajectory
    ]
    seen = {s.step_id for s in steps}
    steps += [
        RunStep(step_id=p["step_id"], edge=p["edge"], executed=False, decision=decisions.get(p["step_id"]))
        for p in state.proposals
        if p["step_id"] not in seen
    ]
    return GraphRun(
        run_id=run.id,
        status=run.status,
        mode=run.mode,
        verified=verified if isinstance(verified, bool) else None,
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        steps=steps,
    )


def demotion_reason(edge: dict) -> str | None:
    """Automatic: any denial or a write whose effect was not seen takes an `auto` edge back to `confirm`."""
    if edge["policy"] != "auto":
        return None
    hit = [k for k in DEMOTION_REASONS if edge["stats"].get(k, 0) > 0]
    return f"{' and '.join(hit)} > 0" if hit else None


def _apply_tier(e: dict, promote: set[str], *, fde: bool, criteria: list[dict], today) -> tuple[list[EdgeChange], Proposal | None]:
    """A v3 edge: automatic demotion one tier down, else a code-computed proposal one tier up,
    applied only when asked — and past `ask` only by the FDE after the cooling period."""
    changes: list[EdgeChange] = []
    cur = e.get("tier") or "shadow"
    down = tiers.demotion(e)
    if down:
        to, reason = down
        changes.append(EdgeChange(edge_id=e["id"], kind="tier", before=cur, after=to, reason=reason))
        e["tier"], e["tier_since"] = to, today.isoformat()
        e["policy"] = stricter(e["policy"], TIER_POLICY[to])
        return changes, None
    covered = tiers.covering_read_back(e, criteria)
    prop = tiers.proposal(e, covered, today)
    if prop is None:
        return changes, None
    proposal = Proposal(
        edge_id=e["id"],
        from_policy=e["policy"],
        to_policy=TIER_POLICY[prop["to"]],
        reason=prop["reason"],
        from_tier=cur,
        to_tier=prop["to"],
        needs_fde=prop["needs_fde"],
        cooled=prop["cooled"],
        ceiling=prop["ceiling"],
    )
    if e["id"] in promote:
        if proposal.needs_fde and not fde:
            raise HTTPException(403, f"Promoting {e['id']} to {proposal.to_tier} is an FDE decision")
        if proposal.needs_fde and not proposal.cooled:
            raise HTTPException(409, f"{e['id']} is still in its cooling period ({tiers.COOLING_DAYS} days)")
        who = "FDE" if proposal.needs_fde else "admin"
        changes.append(EdgeChange(edge_id=e["id"], kind="tier", before=cur, after=proposal.to_tier, reason=f"promoted by {who}"))
        e["tier"], e["tier_since"] = proposal.to_tier, today.isoformat()
        e["policy"] = proposal.to_policy
    return changes, proposal


def _apply_policies(
    draft: dict, promote: set[str], *, fde: bool = False, criteria: list[dict] | None = None
) -> tuple[list[EdgeChange], list[Proposal]]:
    """Mutates `draft` edges in place: demotions always, promotions only where code proposes them
    *and* the admin asked. Returns the policy changes applied and the proposals (asked or not)."""
    changes: list[EdgeChange] = []
    proposals: list[Proposal] = []
    today = datetime.now(UTC).date()
    for e in draft["edges"]:
        if e.get("tier"):
            tier_changes, proposal = _apply_tier(e, promote, fde=fde, criteria=criteria or [], today=today)
            changes += tier_changes
            if proposal:
                proposals.append(proposal)
            continue
        reason = demotion_reason(e)
        if reason:
            changes.append(EdgeChange(edge_id=e["id"], kind="policy", before=e["policy"], after="confirm", reason=reason))
            e["policy"] = stricter(e["policy"], "confirm")
            continue
        if e["policy"] == "confirm" and e["action_class"] != "submit" and promotable(e):
            s = e["stats"]
            proposals.append(
                Proposal(
                    edge_id=e["id"],
                    from_policy="confirm",
                    to_policy="auto",
                    reason=(
                        f"{s['executed']} executed, {s['verified_ok']} verified, {s['denied']} denied, {s['effect_missing']} effect missing"
                    ),
                )
            )
            if e["id"] in promote:
                changes.append(EdgeChange(edge_id=e["id"], kind="policy", before="confirm", after="auto", reason="promoted by admin"))
                e["policy"] = "auto"
    return changes, proposals


def shadow_report(runs: list[WorkflowRun]) -> ShadowReport:
    """Recorder proposed, employee acted, code-scored agreement — per edge, with every disagreement."""
    per_edge: dict[str, ShadowEdgeReport] = {}
    seen_runs = 0
    for run in runs:
        entries = PlannerState.from_checkpoint(run.checkpoint).recovery.get("shadow") or []
        if not entries:
            continue
        seen_runs += 1
        for x in entries:
            rep = per_edge.setdefault(x["edge"], ShadowEdgeReport(edge_id=x["edge"], proposed=0, agreed=0, agreement=0.0, disagreements=[]))
            rep.proposed += 1
            if x.get("agree"):
                rep.agreed += 1
            elif len(rep.disagreements) < 50:
                rep.disagreements.append(ShadowDisagreement(run_id=run.id, edge_id=x["edge"], frm=x["from"], acted=x.get("acted")))
    for rep in per_edge.values():
        rep.agreement = round(rep.agreed / rep.proposed, 3) if rep.proposed else 0.0
    proposed = sum(r.proposed for r in per_edge.values())
    agreed = sum(r.agreed for r in per_edge.values())
    return ShadowReport(
        runs=seen_runs,
        proposed=proposed,
        agreed=agreed,
        agreement=round(agreed / proposed, 3) if proposed else None,
        edges=sorted(per_edge.values(), key=lambda r: r.edge_id),
    )


def accepted_edge(graph: dict, disagreement: ShadowDisagreement, today) -> dict:
    """The structural delta an accepted disagreement is: the proposed edge's move (class, control,
    slot, descriptor, irreversibility) from `frm` to the node the employee actually reached, born at
    `shadow`/`always_ask` with the shadow run as provenance. Never lowers the code-assigned class."""
    src = next(e for e in graph["edges"] if e["id"] == disagreement.edge_id)
    acted = disagreement.acted
    if acted is None or acted not in {n["key"] for n in graph["nodes"]}:
        raise HTTPException(422, f"Disagreement on {disagreement.edge_id} has no destination in this graph")
    new = {
        "id": edge_id(src["frm"], acted, src["action_class"], src.get("control"), src.get("slot")),
        "frm": src["frm"],
        "to": acted,
        "action_class": src["action_class"],
        "control": src.get("control"),
        "slot": src.get("slot"),
        "produces": list(src.get("produces") or []),
        "effect": [],
        "stats": {**empty_stats(), "support": 1},
        "provenance": [{"source": "run", "id": str(disagreement.run_id), "event_ids": ["shadow"]}],
        "policy": "always_ask",
        "tier": "shadow",
        "tier_since": today.isoformat(),
    }
    for k in ("descriptor", "irreversibility", "commit", "anchor_ref"):
        if src.get(k) is not None:
            new[k] = src[k]
    return new


def _accept_disagreements(draft: dict, report: ShadowReport, accept: list[DisagreementAccept], *, fde: bool, today) -> list[EdgeChange]:
    if not accept:
        return []
    if not fde:
        raise HTTPException(403, "Accepting a shadow disagreement into the structure is an FDE decision")
    known = {(d.edge_id, d.acted): d for rep in report.edges for d in rep.disagreements}
    changes: list[EdgeChange] = []
    for a in accept:
        d = known.get((a.edge_id, a.acted))
        if d is None:
            raise HTTPException(422, f"No shadow run disagreed on {a.edge_id} towards {a.acted}")
        new = accepted_edge(draft, d, today)
        if any(e["id"] == new["id"] for e in draft["edges"]):
            continue
        draft["edges"].append(new)
        reason = f"accepted by FDE from shadow disagreement on {a.edge_id}"
        changes.append(EdgeChange(edge_id=new["id"], kind="policy", before="", after="always_ask", reason=reason))
    return changes


def compile_report(graph: dict) -> CompileReport:
    edges = graph["edges"]
    criteria = list((graph.get("goal") or {}).get("criteria") or [])
    writes = [e for e in edges if tiers.is_write(e)]
    out: dict[str, int] = {}
    for e in edges:
        out[e["frm"]] = out.get(e["frm"], 0) + 1
    classes: dict[str, int] = {}
    for e in edges:
        if e.get("irreversibility"):
            classes[e["irreversibility"]] = classes.get(e["irreversibility"], 0) + 1
    recordings = sorted({p["id"] for e in edges for p in e["provenance"] if p["source"] == "recording"})
    run_ids = {p["id"] for e in edges for p in e["provenance"] if p["source"] == "run"}
    vocab_json = graph.get("vocabulary")
    vocab = Vocabulary.from_json(vocab_json) if vocab_json else Vocabulary()
    check = leakage.check({k: v for k, v in graph.items() if k != "vocabulary"}, leakage.RecordingContext.build(vocab=vocab))
    return CompileReport(
        compiled_by=graph["compiled_by"],
        recordings=recordings,
        runs=len(run_ids),
        states=len(graph["nodes"]),
        moves=len(edges),
        irreversibility=classes,
        slots=[CompileSlot(**s) for s in graph.get("slot_table") or []],
        aliases={e["id"]: list(e["descriptor"]["aliases"]) for e in edges if e.get("descriptor") and e["descriptor"].get("aliases")},
        criteria=[
            CompileCriterion(
                type=c["type"],
                slot=c["slot"],
                source=c.get("source", "draft"),
                read_back_via=c.get("read_back_via"),
                covers=[w["id"] for w in writes if c["type"] == "read_back" and tiers.covering_read_back(w, [c])],
            )
            for c in criteria
        ],
        uncovered_writes=[w["id"] for w in writes if not tiers.covering_read_back(w, criteria)],
        vocabulary_size=len((vocab_json or {}).get("words") or []),
        under_segmented=sum(1 for n in out.values() if n > UNDER_SEGMENTED_OUT),
        leakage_ok=check.ok,
        leakage_strings_checked=check.strings_checked,
    )


def _fold(
    version: WorkflowVersion,
    runs: list[WorkflowRun],
    promote: set[str],
    *,
    fde: bool = False,
    accept: list[DisagreementAccept] | None = None,
) -> tuple[dict | None, list[GraphRun], list[EdgeChange], list[Proposal]]:
    definition = version.definition
    graph = definition.get("graph")
    views: list[GraphRun] = []
    deltas: list[dict] = []
    for run in runs:
        state = PlannerState.from_checkpoint(run.checkpoint)
        view = _run_view(run, state)
        views.append(view)
        if graph is not None:
            delta = merge_run(definition, state, str(run.id), view.verified, leakage_failed=int(state.recovery.get("leakage_failed", 0)))
            if delta is not None:
                deltas.append(delta)
    if graph is None:
        return None, views, [], []
    draft = merge_graphs([graph, *deltas])
    draft["trajectories"] = int(graph["trajectories"])  # runs are statistics, not recorded trajectories
    before = {e["id"]: e for e in graph["edges"]}
    changes = [
        EdgeChange(edge_id=e["id"], kind="stats", before=before[e["id"]]["stats"], after=e["stats"])
        for e in draft["edges"]
        if e["id"] in before and e["stats"] != before[e["id"]]["stats"]
    ]
    criteria = list((graph.get("goal") or {}).get("criteria") or [])
    policy_changes, proposals = _apply_policies(draft, promote, fde=fde, criteria=criteria)
    accepted = _accept_disagreements(draft, shadow_report(runs), accept or [], fde=fde, today=datetime.now(UTC).date())
    return draft, views, changes + policy_changes + accepted, proposals


def _previous_approved(session: Session, workflow: Workflow, version: WorkflowVersion) -> WorkflowVersion | None:
    stmt = (
        select(WorkflowVersion)
        .join(WorkflowApproval, WorkflowApproval.version_id == WorkflowVersion.id)
        .where(WorkflowVersion.workflow_id == workflow.id, WorkflowVersion.number < version.number, WorkflowApproval.decision == "approved")
        .order_by(WorkflowVersion.number.desc())
        .limit(1)
    )
    return session.scalar(stmt)


def _structural_diff(prev: dict | None, cur: dict | None) -> list[EdgeChange]:
    """Policy changes between two stored graphs — what an approval of `cur` would newly allow or forbid."""
    if not prev or not cur:
        return []
    before = {e["id"]: e for e in prev["edges"]}
    out: list[EdgeChange] = []
    for e in cur["edges"]:
        p = before.get(e["id"])
        if p is None:
            out.append(EdgeChange(edge_id=e["id"], kind="policy", before="", after=e["policy"], reason="new edge"))
        elif p["policy"] != e["policy"]:
            out.append(EdgeChange(edge_id=e["id"], kind="policy", before=p["policy"], after=e["policy"]))
        elif (p.get("tier") or e.get("tier")) and p.get("tier") != e.get("tier"):
            out.append(EdgeChange(edge_id=e["id"], kind="tier", before=p.get("tier") or "", after=e.get("tier") or ""))
    return out


def review(session: Session, workflow: Workflow, version: WorkflowVersion, *, fde: bool = False) -> GraphReviewOut:
    runs = _runs(session, version)
    draft, views, changes, proposals = _fold(version, runs, set())
    previous = _previous_approved(session, workflow, version) if version.number > 1 else None
    graph = version.definition.get("graph")
    status = service.version_out(version, service.decision_for(session, version)).status
    return GraphReviewOut(
        version_id=version.id,
        version_number=version.number,
        status=status,
        graph=graph,
        runs=views,
        draft=draft if changes or proposals else None,
        changes=changes,
        proposals=proposals,
        shadow=shadow_report(runs),
        compile=compile_report(graph) if graph is not None else None,
        previous_version_number=previous.number if previous else None,
        against_previous=_structural_diff(previous.definition.get("graph") if previous else None, graph),
        can_draft=graph is not None and status == "approved" and version.number == workflow.latest_version and bool(changes or proposals),
        fde=fde,
    )


def create_draft(
    session: Session, workflow: Workflow, version: WorkflowVersion, user_id: uuid.UUID, body: GraphDraftCreate, *, fde: bool = False
) -> VersionOut:
    """The derived draft, made a real version (still unapproved) with the promotions the admin chose.
    `fde` is the server's finding about the requester, never the body's."""
    if body.expected_version != workflow.latest_version or version.number != workflow.latest_version:
        raise HTTPException(409, "The workflow has a newer version; reload before drafting")
    if service.decision_for(session, version) is None or service.decision_for(session, version).decision != "approved":
        raise HTTPException(409, "Only an approved version's runs can be folded into a draft")
    if version.definition.get("graph") is None:
        raise HTTPException(409, "This version has no task graph")
    promote = set(body.promote)
    draft, _, changes, proposals = _fold(version, _runs(session, version), promote, fde=fde, accept=body.accept)
    proposed = {p.edge_id for p in proposals}
    if promote - proposed:
        raise HTTPException(422, f"Edges not proposed for promotion: {sorted(promote - proposed)}")
    if not changes and not proposals:
        raise HTTPException(409, "No run has changed this graph since it was approved")
    definition = WorkflowDefinition.model_validate({**version.definition, "graph": draft})
    return service.create_version(session, workflow, user_id, VersionCreate(expected_version=body.expected_version, definition=definition))
