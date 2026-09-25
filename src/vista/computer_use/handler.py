"""The `execute_workflow` job: the Computer Use Agent's step loop on the worker.

One job runs the loop until the run finishes, pauses for a person, or needs the recorder.
It is idempotent per run: the planner state lives in `workflow_runs.checkpoint`, step ids are
deterministic per `(run, seq)`, and a resumed job finds the recorder's answers in the mailbox
instead of filing them again. A run-level lease keeps two workers from planning at once,
because the job queue only claims and never leases.
"""

from __future__ import annotations

import socket
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select, update

from vista.agents.jev import judge
from vista.computer_use import tools
from vista.computer_use.graph import merge_run, plan_graph_step
from vista.computer_use.harness import (
    CONTROL_PRIMITIVES,
    LOCAL_KINDS,
    REMOTE_KINDS,
    Action,
    HarnessSuspended,
    Observation,
    ObserveContext,
    rank_candidates,
)
from vista.computer_use.harness_local import DocumentsHarness, HttpHarness, WorkspaceHarness
from vista.computer_use.harness_remote import RemoteHarness
from vista.computer_use.planner import Act, Finish, Pause, PlannerState, Stop, facts_from_result, plan_step, verify
from vista.computer_use.run_v3 import is_v3, plan_v3_step, record_read_back
from vista.computer_use.service import JOB_KIND, TERMINAL
from vista.computer_use.verify_v3 import verify_v3
from vista.config import settings
from vista.db import platform_session, tenant_session
from vista.jobs.queue import enqueue
from vista.models.platform import FirmCompany, Job
from vista.models.tenant import (
    AgentRun,
    HarnessConnection,
    HarnessSession,
    HarnessStep,
    Task,
    UsageEvent,
    Workflow,
    WorkflowRun,
    WorkflowVersion,
)
from vista.portfolio.service import next_ref

MAX_CONSECUTIVE_FAILURES = 2


def _now() -> datetime:
    return datetime.now(UTC)


def _lease_owner() -> str:
    return f"{socket.gethostname()}:{uuid.uuid4().hex[:6]}"[:64]


def acquire_lease(session, run_id: uuid.UUID, owner: str) -> bool:
    result = session.execute(
        update(WorkflowRun)
        .where(WorkflowRun.id == run_id, (WorkflowRun.lease_until.is_(None)) | (WorkflowRun.lease_until < _now()))
        .values(lease_until=_now() + timedelta(seconds=settings.computer_use_lease_s), lease_owner=owner)
    )
    session.commit()
    return result.rowcount == 1


def release_lease(session, run: WorkflowRun, owner: str) -> None:
    if run.lease_owner == owner:
        run.lease_until = None
        run.lease_owner = None


class Ledger:
    """The ledger helpers bound to one run, importing handlers lazily (handlers imports us)."""

    def __init__(self, session, agent_run: AgentRun, tenant_schema: str):
        from vista.jobs.handlers import _emit, _next_seq, _record_usage

        self.session = session
        self.agent_run = agent_run
        self.seq = _next_seq(session, agent_run.id)
        self._emit = _emit
        self._record_usage = _record_usage
        self.tenant_schema = tenant_schema

    def emit(self, event_type: str, data: dict) -> None:
        self.seq = self._emit(self.session, self.agent_run.id, self.seq, event_type, data)

    def usage(self, judgment) -> None:
        if judgment.source != "code":
            self._record_usage(self.session, self.agent_run, judgment.model, judgment.input_tokens, judgment.output_tokens)


def run_cost(session, agent_run_id: uuid.UUID) -> Decimal:
    return session.scalar(select(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).where(UsageEvent.run_id == agent_run_id)) or Decimal(0)


def create_task(session, run: WorkflowRun, workflow_name: str, title: str, description: str, priority: str = "High") -> str | None:
    """A task for a person, deduped per run + title so retries never create two."""
    existing = session.scalar(
        select(Task.ref).where(Task.source_type == "agent_run", Task.source_id == str(run.agent_run_id), Task.title == title)
    )
    if existing:
        return existing
    with platform_session() as platform:
        firm_id = platform.scalar(select(FirmCompany.firm_id).where(FirmCompany.id == run.company_id))
        ref = next_ref(platform, firm_id, "task", "T") if firm_id else None
        platform.commit()
    if ref is None:
        return None
    session.add(
        Task(
            company_id=run.company_id,
            ref=ref,
            title=title,
            description=description,
            category="Automation",
            source_type="agent_run",
            source_id=str(run.agent_run_id),
            priority=priority,
            status="Open",
            created_by="Computer Use Agent",
        )
    )
    session.flush()
    return ref


def build_harnesses(
    session, tenant_schema: str, run: WorkflowRun, workflow: Workflow, kinds: set[str], replay: dict[int, dict], limits_left: dict
) -> dict[str, object]:
    harnesses: dict[str, object] = {}
    inputs = run.inputs or {}
    if "documents" in kinds:
        harnesses["documents"] = DocumentsHarness(session, tenant_schema, inputs)
    if "http" in kinds:
        rows = session.scalars(
            select(HarnessConnection).where(HarnessConnection.company_id == run.company_id, HarnessConnection.status == "active")
        ).all()
        harnesses["http"] = HttpHarness([{"name": r.name, "config": r.config} for r in rows])
    if "workspace" in kinds:
        harnesses["workspace"] = WorkspaceHarness(session, platform_session, run.company_id, run.agent_run_id, workflow.name)
    hs = session.get(HarnessSession, run.harness_session_id) if run.harness_session_id else None
    for kind in sorted(kinds & REMOTE_KINDS):
        harnesses[kind] = RemoteHarness(
            kind,
            session,
            run,
            hs,
            replay,
            workflow_name=workflow.name,
            timeout_s=settings.computer_use_step_timeout_s,
            limits_left=limits_left,
        )
    return harnesses


def compose_observation(harnesses: dict[str, object], ctx: ObserveContext, focus: str | None) -> Observation:
    """What the planner sees: the screen (a remote harness) when there is one, plus every local
    candidate — documents, records, endpoints, the task slot — so one judgment can pick any."""
    primary = harnesses[focus].observe(ctx) if focus else None
    candidates = list(primary.candidates) if primary else []
    facts = dict(primary.facts) if primary else {}
    for kind, harness in harnesses.items():
        if kind == focus or kind in REMOTE_KINDS:
            continue
        local = harness.observe(ctx)
        candidates.extend(local.candidates)
        for k, v in local.facts.items():
            facts.setdefault(f"{kind}_{k}", v)
    return Observation(
        primary.harness if primary else "local",
        facts,
        rank_candidates(candidates),
        observation_id=primary.observation_id if primary else None,
    )


def route(harnesses: dict[str, object], action: Action, focus: str | None):
    tagged = action.target.attrs.get("harness") if action.target else None
    if tagged and tagged in harnesses:
        return harnesses[tagged]
    if action.primitive == "create_task" and "workspace" in harnesses:
        return harnesses["workspace"]
    if action.primitive == "http_get" and "http" in harnesses:
        return harnesses["http"]
    if focus:
        return harnesses[focus]
    return next(iter(harnesses.values()))


def handle_execute_workflow(job: Job, tenant_schema: str) -> None:
    run_id = uuid.UUID(job.payload["workflow_run_id"])
    owner = _lease_owner()
    with tenant_session(tenant_schema) as session:
        if not acquire_lease(session, run_id, owner):
            return
        run = session.get(WorkflowRun, run_id)
        if run is None:
            raise RuntimeError(f"workflow run {run_id} not found in {tenant_schema}")
        try:
            _execute(session, tenant_schema, job, run, owner)
        finally:
            run = session.get(WorkflowRun, run_id)
            if run is not None:
                release_lease(session, run, owner)
                session.commit()


def _execute(session, tenant_schema: str, job: Job, run: WorkflowRun, owner: str) -> None:
    if run.status in TERMINAL:
        return
    workflow = session.get(Workflow, run.workflow_id)
    version = session.get(WorkflowVersion, run.workflow_version_id)
    agent_run = session.get(AgentRun, run.agent_run_id)
    from vista.jobs.handlers import _start_run

    _start_run(session, run.agent_run_id, tenant_schema)
    ledger = Ledger(session, agent_run, tenant_schema)
    definition = version.definition
    limits = run.limits or {}

    def finish(status: str, *, error: str | None = None, outcome: dict | None = None, extra: dict | None = None) -> None:
        run.status = status
        run.finished_at = _now()
        run.error = error
        run.cost_usd = run_cost(session, run.agent_run_id)
        run.pending = None
        run.pending_step_id = None
        if outcome is not None:
            run.outcome = outcome
        agent_run.status = "succeeded" if status == "succeeded" else status
        agent_run.finished_at = run.finished_at
        agent_run.error = error
        delta = (
            {}
            if outcome is not None and "finding_id" in outcome
            else {"graph_delta": merge_run(definition, PlannerState.from_checkpoint(run.checkpoint), str(run.id), None)}
        )
        ledger.emit(
            "result",
            {
                "status": status,
                "steps_used": run.steps_used,
                "cost_usd": str(run.cost_usd or 0),
                "error": error,
                **(outcome or {}),
                **(extra or {}),
                **delta,
            },
        )
        session.commit()

    # --- stop requested (kill switch) ---
    if run.stop_requested:
        finish("stopped", error=run.error or "stop requested")
        return

    # --- the approval is still the one we were launched under ---
    from vista.automation.service import decision_for

    approval = decision_for(session, version)
    if (
        version.definition_hash != run.definition_hash
        or approval is None
        or approval.decision != "approved"
        or approval.definition_hash != run.definition_hash
    ):
        ledger.emit("error", {"error": "definition_hash_mismatch"})
        finish("failed", error="definition_hash_mismatch")
        return

    state = PlannerState.from_checkpoint(run.checkpoint)
    if not run.checkpoint:
        ledger.emit(
            "step",
            {
                "message": "started",
                "workflow_run_id": str(run.id),
                "version_id": str(version.id),
                "definition_hash": run.definition_hash,
                "mode": run.mode,
                "parent_run_id": None,
            },
        )
        run.started_at = run.started_at or _now()
        run.deadline_at = run.started_at + timedelta(seconds=int(limits.get("max_runtime_seconds", 300)))
    run.status = "running"
    agent_run.status = "running"
    session.commit()

    allowed = definition["allowed_tools"]
    primitives = tools.primitives_for(allowed)
    # Local kinds come from the registry; remote kinds only from the session the employee
    # actually claimed (a tool with a local alternative never waits for a recorder).
    kinds = tools.kinds_for(allowed) & LOCAL_KINDS
    if run.harness_session_id is not None:
        hs_ = session.get(HarnessSession, run.harness_session_id)
        kinds |= set(hs_.kinds or []) if hs_ is not None else set()
    elif (run.pending or {}).get("kind") == "offer" and (run.pending or {}).get("harness_kinds"):
        run.status = "waiting_for_harness"
        agent_run.status = "waiting"
        session.commit()
        return

    # --- a step the recorder never answered ---
    if run.pending_step_id is not None:
        step = session.get(HarnessStep, run.pending_step_id)
        if step is not None and step.status in ("pending", "claimed"):
            if step.expires_at > _now():
                run.status = "waiting_for_harness"
                agent_run.status = "waiting"
                session.commit()
                return
            step.status = "expired"
            step.completed_at = _now()
            ledger.emit("error", {"error": "harness_timeout", "step_id": str(step.id), "seq": step.seq})
            state.history.append(
                {
                    "step_id": str(step.id),
                    "seq": step.seq,
                    "primitive": (step.request or {}).get("action"),
                    "executed": False,
                    "ok": False,
                    "error": "harness_timeout",
                }
            )
            state.n = max(state.n, step.seq)
            state.observation = None
            run.pending_step_id = None
            run.pending = None
            hs = session.get(HarnessSession, run.harness_session_id) if run.harness_session_id else None
            if (
                hs is None
                or hs.status != "active"
                or hs.lease_until < _now()
                or sum(1 for h in state.history[-4:] if h.get("error") == "harness_timeout") >= 2
            ):
                run.checkpoint = state.to_checkpoint()
                pause(
                    session,
                    run,
                    agent_run,
                    ledger,
                    workflow,
                    state,
                    {
                        "reason": "harness_unavailable",
                        "description": "The employee's recorder stopped answering.",
                        "seq": state.n,
                        "step_id": str(step.id),
                    },
                )
                return
        elif step is not None and step.status in ("done", "failed", "expired"):
            # Answered (or given up on): the replay below carries the answer; nothing is pending.
            run.pending_step_id = None
            run.pending = None

    replay = {
        s.seq: s.result
        for s in session.scalars(select(HarnessStep).where(HarnessStep.workflow_run_id == run.id, HarnessStep.status == "done")).all()
    }

    def limits_left() -> dict:
        return {
            "steps": max(0, int(limits.get("max_steps", 10)) - state.n),
            "seconds": max(0, int((run.deadline_at - _now()).total_seconds())) if run.deadline_at else None,
        }

    harnesses = build_harnesses(session, tenant_schema, run, workflow, kinds, replay, limits_left())
    # A resumed run plans over the checkpointed observation; the remote harness must cite
    # that observation's id on the next targeted request, or the recorder refuses it as stale.
    last_obs = state.observation or {}
    if last_obs.get("harness") in harnesses and last_obs.get("harness") in REMOTE_KINDS:
        harnesses[last_obs["harness"]].last_observation_id = last_obs.get("observation_id")
    focus = next((k for k in ("browser", "desktop") if k in harnesses), None)
    failures = 0

    while True:
        # --- limits, checked before every step ---
        cost = run_cost(session, run.agent_run_id)
        run.cost_usd = cost
        reason = None
        if state.n >= int(limits.get("max_steps", 10)):
            reason = "step_limit"
        elif run.deadline_at is not None and _now() >= run.deadline_at:
            reason = "runtime_limit"
        elif cost >= Decimal(str(limits.get("max_cost_usd", "1.00"))):
            reason = "budget_exhausted"
        if reason:
            ledger.emit("error", {"error": reason, "steps_used": state.n, "cost_usd": str(cost)})
            create_task(
                session,
                run,
                workflow.name,
                f"Workflow “{workflow.name}” run stopped: {reason.replace('_', ' ')}",
                "The run hit one of its limits before finishing; a person should check what was done "
                "and decide whether to raise the limit or split the workflow.",
            )
            run.checkpoint = state.to_checkpoint()
            finish("failed", error=reason)
            return
        if run.stop_requested:
            run.checkpoint = state.to_checkpoint()
            finish("stopped", error=run.error or "stop requested")
            return

        seq = state.n + 1
        from vista.computer_use.planner import step_id_for

        step_id = step_id_for(str(run.id), seq)
        ctx = ObserveContext(step_id, seq, definition["goal"], run.inputs or {})

        # --- observe ---
        try:
            if state.observation:
                observation = Observation.from_json(state.observation)
            else:
                observation = compose_observation(harnesses, ctx, focus)
                ledger.emit(
                    "tool_call",
                    {
                        "tool": "observe",
                        "harness": observation.harness,
                        "step_id": step_id,
                        "seq": seq,
                        "candidates": len(observation.candidates),
                        "facts": {k: v for k, v in observation.facts.items() if k in ("url", "title", "app", "window_title")},
                    },
                )
                if focus:
                    # An explicit observe consumed a step number for the remote harness.
                    state.n = seq
                    state.history.append({"step_id": step_id, "seq": seq, "primitive": "observe", "executed": True, "ok": True})
                    seq = state.n + 1
                    step_id = step_id_for(str(run.id), seq)
                state.observation = observation.to_json()
                run.checkpoint = state.to_checkpoint()
                session.commit()
        except HarnessSuspended as suspended:
            suspend(session, run, agent_run, ledger, state, suspended, job)
            return

        # --- judge ---
        pending = state.pending or {}
        if pending.get("action", {}).get("seq") == seq and seq in replay:
            # The recorder answered the action already decided (and, if gated, approved) for
            # this step: apply that answer rather than asking the model again.
            decision = Act(Action.from_json(pending["action"]), gated=bool(pending.get("gated")))
        else:
            state.pending = None
            planner = plan_v3_step if is_v3(definition, observation) else plan_graph_step if definition.get("graph") else plan_step
            decision, judgment, detail = planner(
                state,
                definition,
                run.inputs or {},
                observation,
                run_id=str(run.id),
                primitives=primitives & set().union(*(h.capabilities() for h in harnesses.values())) | CONTROL_PRIMITIVES,
                limits_left=limits_left(),
                mode=run.mode,
                risk_threshold=settings.computer_use_risk_threshold,
                judge_fn=judge,
            )
            ledger.emit(
                "model_call",
                {
                    "model": judgment.model,
                    "source": judgment.source,
                    "input_tokens": judgment.input_tokens,
                    "output_tokens": judgment.output_tokens,
                    **detail,
                },
            )
            ledger.usage(judgment)
            run.cost_usd = run_cost(session, run.agent_run_id)
            run.checkpoint = state.to_checkpoint()
            session.commit()

        if isinstance(decision, Pause):
            pause(session, run, agent_run, ledger, workflow, state, decision.request)
            return
        if isinstance(decision, Stop):
            run.checkpoint = state.to_checkpoint()
            finish("stopped", error=f"denied at step {decision_seq(state)}")
            return
        if isinstance(decision, Finish):
            conclude(session, tenant_schema, run, agent_run, ledger, workflow, definition, state, observation, decision.reason, finish)
            return

        # --- act ---
        action = decision.action
        harness = route(harnesses, action, focus)
        state.pending = {"action": action.to_json(), "gated": decision.gated}
        try:
            result = harness.act(action)
        except HarnessSuspended as suspended:
            suspend(session, run, agent_run, ledger, state, suspended, job)
            return
        state.pending = None
        state.n = action.seq
        run.steps_used = state.n
        entry = {
            "step_id": action.step_id,
            "seq": action.seq,
            "primitive": action.primitive,
            "harness": getattr(harness, "kind", "?"),
            "target": action.target.label if action.target else None,
            "value_input": action.value_input,
            "executed": True,
            "ok": result.ok,
            "description": result.description,
            "error": result.error,
        }
        state.history.append(entry)
        if result.facts:
            record_read_back(state, result.facts)
            if result.facts.get("leakage_failed"):
                state.recovery["leakage_failed"] = int(state.recovery.get("leakage_failed", 0)) + 1
            state.facts[f"step {action.seq}"] = facts_from_result(result.facts)
        if result.undo:
            state.undo.append({"step_id": action.step_id, "undo": result.undo})
        state.observation = result.observation.to_json() if result.observation else None
        ledger.emit(
            "tool_call",
            {
                **entry,
                "tool": action.primitive,
                "gated": decision.gated,
                "dry_run": run.mode == "dry_run",
                "facts": {k: (v if not isinstance(v, list) else f"{len(v)} rows") for k, v in facts_from_result(result.facts).items()},
                "undo": result.undo,
                "artifact_key": result.artifact_key,
            },
        )
        failures = 0 if result.ok else failures + 1
        run.checkpoint = state.to_checkpoint()
        run.lease_until = _now() + timedelta(seconds=settings.computer_use_lease_s)
        session.commit()
        if failures >= MAX_CONSECUTIVE_FAILURES:
            pause(
                session,
                run,
                agent_run,
                ledger,
                workflow,
                state,
                {
                    "reason": "harness_error",
                    "description": f"The last {failures} actions failed: {result.error}",
                    "seq": action.seq,
                    "step_id": action.step_id,
                },
            )
            return


def decision_seq(state: PlannerState) -> int:
    return state.n + 1


def suspend(
    session, run: WorkflowRun, agent_run: AgentRun, ledger: Ledger, state: PlannerState, suspended: HarnessSuspended, job: Job
) -> None:
    run.status = "waiting_for_harness"
    run.pending_step_id = uuid.UUID(suspended.step_id)
    run.pending = {
        "kind": "step",
        "step_id": suspended.step_id,
        "seq": suspended.request.get("seq"),
        "harness": suspended.request.get("harness"),
        "action": suspended.request.get("action"),
        "description": suspended.request.get("description"),
    }
    run.checkpoint = state.to_checkpoint()
    agent_run.status = "waiting"
    ledger.emit(
        "step",
        {
            "message": "waiting_for_harness",
            "step_id": suspended.step_id,
            "seq": suspended.request.get("seq"),
            "harness": suspended.request.get("harness"),
            "action": suspended.request.get("action"),
            "description": suspended.request.get("description"),
            "expires_at": suspended.expires_at.isoformat(),
        },
    )
    ledger.emit(
        "tool_call",
        {
            "tool": suspended.request.get("action"),
            "harness": suspended.request.get("harness"),
            "step_id": suspended.step_id,
            "seq": suspended.request.get("seq"),
            "target": (suspended.request.get("target_id")),
            "value_input": None,
            "executed": False,
            "filed": True,
            "description": suspended.request.get("description"),
        },
    )
    session.commit()
    with platform_session() as platform:
        enqueue(
            platform,
            tenant_id=job.tenant_id,
            kind=JOB_KIND,
            payload={"workflow_run_id": str(run.id), "watchdog": suspended.step_id},
            idempotency_key=f"workflow_run:{run.id}:watchdog:{suspended.request.get('seq')}",
            run_at=suspended.expires_at + timedelta(seconds=5),
        )
        platform.commit()


def pause(session, run: WorkflowRun, agent_run: AgentRun, ledger: Ledger, workflow: Workflow, state: PlannerState, request: dict) -> None:
    run.status = "waiting_for_human"
    run.pending = {"kind": "decision", **request}
    run.pending_step_id = uuid.UUID(request["step_id"]) if request.get("step_id") else None
    run.checkpoint = state.to_checkpoint()
    agent_run.status = "waiting"
    ledger.emit("step", {"message": "waiting_for_human", **{k: v for k, v in request.items() if k != "candidates"}})
    ledger.emit("handoff", {"to": "owner", "pending_review": True, "step_id": request.get("step_id"), "reason": request.get("reason")})
    ref = create_task(
        session,
        run,
        workflow.name,
        f"Decide step {request.get('seq')} of workflow “{workflow.name}”",
        f"{request.get('description', '')} Reason: {request.get('reason')}. "
        "Approve, deny or stop the run under Workflows in the company workspace.",
    )
    if ref:
        run.pending = {**run.pending, "task_ref": ref}
    session.commit()


def conclude(
    session,
    tenant_schema: str,
    run: WorkflowRun,
    agent_run: AgentRun,
    ledger: Ledger,
    workflow: Workflow,
    definition: dict,
    state: PlannerState,
    observation: Observation,
    reason: str,
    finish,
) -> None:
    from vista.jobs.handlers import _finding

    if reason == "dry_run_stopped_before_write":
        outcome = {"verified": False, "reason": reason, "criteria": [], "goal_met": None}
        run.checkpoint = state.to_checkpoint()
        finish("succeeded", outcome=outcome)
        return
    verification = (
        verify_v3(definition, observation, state, judge_fn=judge)
        if observation is not None and is_v3(definition, observation)
        else verify(definition, observation, state.facts, judge_fn=judge)
    )
    ledger.emit(
        "model_call",
        {
            "model": verification.judgment.model,
            "source": verification.judgment.source,
            "input_tokens": verification.judgment.input_tokens,
            "output_tokens": verification.judgment.output_tokens,
            "phase": "verify",
            **verification.to_json(),
        },
    )
    ledger.usage(verification.judgment)
    run.cost_usd = run_cost(session, run.agent_run_id)
    passed = verification.passed
    status = "succeeded" if passed else "failed"
    outcome = {
        "verified": passed,
        "goal_met": verification.goal_met,
        "p_goal": verification.p_goal,
        "criteria": verification.criteria,
        "matched": sum(1 for c in verification.criteria if c["met"]),
        "checked": len(verification.criteria),
    }
    finding = _finding(
        agent_run,
        kind="observed_fact",
        finding_type="workflow.execution",
        title=f"Workflow run: {workflow.name} — {'verified' if passed else 'not verified'}",
        detail=(
            f"{state.n} step(s) in {run.mode} mode; {outcome['matched']} of {outcome['checked']} success criteria judged met "
            f"(goal met: {verification.p_goal:.0%}). "
            + (
                "Read-back verification passed."
                if passed
                else "Read-back verification did not pass; a person should check the destination."
            )
        ),
        evidence={
            "refs": [f"workflow_run:{run.id}", f"run:{run.agent_run_id}", f"version:{run.workflow_version_id}"],
            "workflow_run_id": str(run.id),
            "definition_hash": run.definition_hash,
            "mode": run.mode,
            "verification": outcome,
            "steps": [h for h in state.history if h.get("executed")],
            "cost_usd": str(run.cost_usd or 0),
            "undo": state.undo,
            "artifacts": [h.get("artifact_key") for h in state.history if h.get("artifact_key")],
            "graph_delta": merge_run(definition, state, str(run.id), passed, leakage_failed=int(state.recovery.get("leakage_failed", 0))),
        },
    )
    session.add(finding)
    session.flush()
    ledger.emit("finding", {"finding_id": str(finding.id), "kind": finding.kind, "title": finding.title})
    outcome["finding_id"] = str(finding.id)
    if not passed:
        create_task(
            session,
            run,
            workflow.name,
            f"Check the result of workflow “{workflow.name}”",
            "Read-back verification did not pass. Compare what the run entered with the source and correct it; "
            "the run trace lists every step and an undo payload per write.",
        )
    run.checkpoint = state.to_checkpoint()
    finish(status, error=None if passed else "verification_failed", outcome=outcome)


def sync_after_terminal(job_id: uuid.UUID) -> None:
    """AFTER_TERMINAL hook: when the worker fails the job permanently, the run header follows."""
    with platform_session() as platform:
        job = platform.get(Job, job_id)
        if job is None or job.status != "failed":
            return
        from vista.models.platform import Tenant

        schema = platform.scalar(select(Tenant.schema_name).where(Tenant.id == job.tenant_id))
        payload = dict(job.payload or {})
        error = job.error
    if not schema or "workflow_run_id" not in payload:
        return
    with tenant_session(schema) as session:
        run = session.get(WorkflowRun, uuid.UUID(payload["workflow_run_id"]))
        if run is None or run.status in TERMINAL:
            return
        run.status = "failed"
        run.error = (error or "job failed")[:2000]
        run.finished_at = _now()
        run.lease_until = None
        run.lease_owner = None
        run.pending = None
        session.commit()
