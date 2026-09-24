"""Harnesses that live on the employee's machine: browser and desktop.

The worker never talks to the recorder. `observe()`/`act()` file a step request in the
`harness_steps` mailbox and raise `HarnessSuspended`; the recorder pulls the request,
performs it, and posts a result; the API enqueues a resume job; on that job the same
call finds its answer in `replay` and returns it. The planner cannot tell the difference.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from vista.computer_use.harness import Action, ActionResult, Candidate, HarnessSuspended, Observation, ObserveContext, rank_candidates
from vista.computer_use.planner import step_id_for

# Page text the recorder sends back; enough for a list plus an open record's detail pane.
MAX_TEXT = 12000

CAPABILITIES = {
    "browser": {"navigate", "click", "type_value", "press", "read", "extract", "submit", "screenshot", "wait"},
    "desktop": {"navigate", "click", "type_value", "press", "submit", "screenshot", "wait"},
}
# planner primitive → the recorder's action vocabulary
RECORDER_ACTION = {
    "navigate": "navigate",
    "click": "click",
    "submit": "click",
    "type_value": "type",
    "press": "press",
    "read": "extract",
    "extract": "extract",
    "screenshot": "screenshot",
    "wait": "wait",
}


def request_for(
    kind: str, action: Action, observation_id: str | None, limits_left: dict, workflow_name: str, run_id: str, timeout_s: int
) -> dict:
    """The JSON the recorder receives. The literal value is included (the recorder has no
    inputs of its own); the ledger records only the input's name."""
    req: dict = {
        "step_id": action.step_id,
        "seq": action.seq,
        "run_id": run_id,
        "workflow": workflow_name,
        "harness": kind,
        "action": RECORDER_ACTION[action.primitive] if action.primitive != "observe" else "observe",
        "description": action.describe() if action.primitive != "observe" else "Look at the screen",
        "limits_remaining": limits_left,
        "timeout_ms": timeout_s * 1000,
    }
    if action.target is not None:
        req["target_id"] = action.target.id
        req["observation_id"] = observation_id
    if action.primitive == "navigate":
        req["value"] = action.args.get("url") or action.args.get("name")
    elif action.primitive == "type_value":
        req["value"] = action.value
        req["replace"] = True
    elif action.primitive == "press":
        req["value"] = action.args.get("key", "Enter")
    elif action.primitive in ("read", "extract"):
        req["value"] = "text" if action.primitive == "read" else "table"
    elif action.primitive == "wait":
        req["value"] = int(action.args.get("ms", 1500))
    return req


def observation_from_result(kind: str, result: dict) -> Observation | None:
    obs = result.get("observation")
    if not obs:
        return None
    candidates = []
    for c in obs.get("candidates") or []:
        try:
            cand = Candidate.from_json(c)
        except (KeyError, TypeError):
            continue
        candidates.append(Candidate(cand.id, cand.role, cand.name, cand.kind, {**cand.attrs, "harness": kind}))
    facts = {k: v for k, v in obs.items() if k in ("url", "title", "app", "window_title", "sensitive")}
    if obs.get("text_excerpt"):
        facts["text"] = str(obs["text_excerpt"])[:MAX_TEXT]
    return Observation(
        kind,
        facts,
        rank_candidates(candidates),
        observation_id=obs.get("observation_id"),
        artifact_key=(result.get("evidence") or {}).get("artifact_key"),
    )


def action_result_from(kind: str, step_id: str, result: dict) -> ActionResult:
    payload = result.get("result") or {}
    facts = {k: v for k, v in payload.items() if k in ("url_after", "title_after", "columns", "rows", "text", "count", "previous_value")}
    if "url_after" in facts:
        facts["url"] = facts.pop("url_after")
    if "title_after" in facts:
        facts["title"] = facts.pop("title_after")
    error = result.get("error")
    return ActionResult(
        step_id,
        bool(result.get("ok")),
        description=str(result.get("description") or ""),
        facts=facts,
        undo=payload.get("undo"),
        artifact_key=(result.get("evidence") or {}).get("artifact_key"),
        error=(error.get("code") if isinstance(error, dict) else error) if error else None,
        observation=observation_from_result(kind, result),
    )


class RemoteHarness:
    kind: str

    def __init__(
        self, kind: str, session, run, harness_session, replay: dict[int, dict], *, workflow_name: str, timeout_s: int, limits_left: dict
    ):
        self.kind = kind
        self.session = session
        self.run = run
        self.harness_session = harness_session
        self.replay = replay
        self.workflow_name = workflow_name
        self.timeout_s = timeout_s
        self.limits_left = limits_left
        self.last_observation_id: str | None = None

    def capabilities(self) -> set[str]:
        return set(CAPABILITIES[self.kind])

    def observe(self, context: ObserveContext) -> Observation:
        answer = self.replay.get(context.seq)
        if answer is not None:
            obs = observation_from_result(self.kind, answer)
            if obs is None:
                raise RuntimeError(f"recorder answered step {context.seq} without an observation")
            self.last_observation_id = obs.observation_id
            return obs
        action = Action(context.step_id, context.seq, "observe")
        self._file(
            context.seq, request_for(self.kind, action, None, self.limits_left, self.workflow_name, str(self.run.id), self.timeout_s)
        )
        raise AssertionError("unreachable")

    def act(self, action: Action) -> ActionResult:
        answer = self.replay.get(action.seq)
        if answer is not None:
            result = action_result_from(self.kind, action.step_id, answer)
            if result.observation is not None:
                self.last_observation_id = result.observation.observation_id
            return result
        self._file(
            action.seq,
            request_for(
                self.kind, action, self.last_observation_id, self.limits_left, self.workflow_name, str(self.run.id), self.timeout_s
            ),
        )
        raise AssertionError("unreachable")

    def close(self) -> None:
        return None

    def _file(self, seq: int, request: dict) -> None:
        from vista.models.tenant import HarnessStep

        expires_at = datetime.now(UTC) + timedelta(seconds=self.timeout_s)
        step = HarnessStep(
            id=uuid.UUID(step_id_for(str(self.run.id), seq)),
            workflow_run_id=self.run.id,
            seq=seq,
            harness_session_id=self.harness_session.id if self.harness_session is not None else None,
            harness=self.kind,
            request=request,
            status="pending",
            expires_at=expires_at,
        )
        self.session.add(step)
        self.session.flush()
        raise HarnessSuspended(str(step.id), expires_at, request)
