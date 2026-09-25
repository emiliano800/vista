"""Run loop v3 over a task graph whose nodes carry L0: code first, Jev only on ambiguity.

A step over a v3 graph and a v3 observation (the sidecar's cloud frame: `l0`, `l1`, per-candidate
normalised `targets`) goes:

1. **Locate** by L0 equality. Exactly one node whose L0 set equals the observation's is the run's
   node, without asking. Several (a graph defect) → Jev picks among *those*; none → recovery.
2. **Offer** only the located node's own edges. Jev never sees another node's moves, so the
   v1 "re-judge" cannot happen; `rejudged` is kept in the checkpoint as a defect counter (target 0).
3. **Target** in code: an edge's descriptor is matched against the live candidates (same role,
   normalised name equal or an alias, landmark agreeing when both are known). Exactly one live
   candidate over `DESCRIPTOR_THRESHOLD` resolves the target; several → Jev picks among them;
   none → re-observe once, then `ambiguous_target`.
4. **Straight line** — no `edge` question — only when the edge's tier is `unattended`, the node
   has exactly one edge whose target resolved in code, and the edge is `navigational`, or
   `mutating` on a declared or transfer-linked slot. Never `committing`. In every other tier every
   step is judged.
5. **Pause** for `committing`, `confirm`/`always_ask` policy, or `p_irreversible ≥ risk_threshold`;
   Jev's `p_irreversible` may raise the class, never lower it.
6. **Recover before pausing, never affirmatively.** An unexpected modal with no textbox and no
   commit-vocabulary primary button is closed with Escape or a cancel/close/dismiss control;
   `in:<unknown>` follows the recorded navigational entry edge from the last node; stale / no
   target re-observes once. Budget `RECOVERY_BUDGET` per run, `off_plan` after. Sign-in, session
   expired, payment and private windows (`sensitive`) always pause. Never click ok / yes /
   confirm / continue.

`effect_seen` is an L0 diff first: the previous edge's effect tokens present in the new L0 settle
it without a question; only when they are absent and the L0 did not change is Jev asked.
"""

from __future__ import annotations

from taskmining.state import COMMIT_VOCAB, raise_irreversibility
from vista.agents.jev import NONE, Judgment, noul, pick
from vista.computer_use.graph import (
    DONE,
    Graph,
    edge_label,
    effect_words,
    node_label,
    resolve_value,
    target_candidates,
    target_question,
)
from vista.computer_use.harness import ALWAYS_GATED, TARGETED, VALUED, WRITE_PRIMITIVES, Action, Candidate, Observation
from vista.computer_use.planner import (
    ACTION_CONFIDENCE,
    DEFAULT_RISK_THRESHOLD,
    TARGET_CONFIDENCE,
    VALUE_CONFIDENCE,
    Act,
    Decision,
    Finish,
    JudgeFn,
    Pause,
    PlannerState,
    Stop,
    _candidate_view,
    _preview,
    step_id_for,
    value_candidates,
)

DESCRIPTOR_THRESHOLD = 0.8  # descriptor match score a live candidate must clear to be the target in code
RECOVERY_BUDGET = 2  # recovery moves per task per run; `off_plan` after
EFFECT_SEEN = 0.5
NODE_CONFIDENCE = 0.5
MAX_EDGE_OFFERS = 12
SHADOW_MAX_WAITS = 20  # observe-only ticks before a shadow proposal is scored as not acted on
TIERS: tuple[str, ...] = ("shadow", "ask", "confirm", "unattended")
DISMISS_WORDS: frozenset[str] = frozenset({"cancel", "close", "dismiss", "×", "x", "no"})
AFFIRMATIVE_WORDS: frozenset[str] = frozenset({"ok", "yes", "confirm", "continue"}) | COMMIT_VOCAB


def is_v3(definition: dict, observation: Observation) -> bool:
    g = definition.get("graph") or {}
    return bool(g.get("nodes")) and all(n.get("l0") is not None for n in g["nodes"]) and observation.facts.get("l0") is not None


def observed_l0(observation: Observation) -> frozenset[str]:
    return frozenset(str(t) for t in observation.facts.get("l0") or [])


# ---- locate ---------------------------------------------------------------------------------------


def locate(graph: Graph, l0: frozenset[str]) -> list[dict]:
    """Nodes whose L0 set equals the observation's, in key order. One is the answer; several is a
    graph defect Jev breaks the tie on; none is `in:<unknown>`."""
    return sorted((n for n in graph.nodes.values() if frozenset(n["l0"]) == l0), key=lambda n: n["key"])


# ---- target resolution in code ---------------------------------------------------------------------


def descriptor_score(descriptor: dict, c: Candidate) -> float:
    """How well a live candidate fits an edge's typed descriptor. Role must agree; the normalised
    name equal → 1.0, one of the aliases → 0.9, same landmark with unknown name → 0.6."""
    role = descriptor.get("role")
    if role and role != "unknown" and c.role != role:
        return 0.0
    if c.attrs.get("disabled"):
        return 0.0
    name = descriptor.get("name")
    aliases = set(descriptor.get("aliases") or [])
    landmark = descriptor.get("landmark") or ""
    c_landmark = str(c.attrs.get("landmark") or "")
    if landmark and c_landmark and landmark != c_landmark:
        return 0.0
    if name and c.name == name:
        return 1.0
    if c.name in aliases:
        return 0.9
    if not name or name == "{text}":
        return 0.6 if landmark and landmark == c_landmark else 0.0
    return 0.0


def needs_target(edge: dict) -> bool:
    """Edges recorded on a key or on the screen itself (`submit` by Cmd+S, `navigate`) act without a control."""
    return edge["action_class"] in TARGETED and (edge.get("descriptor") or {}).get("role") not in ("key", "screen")


def resolve_target(edge: dict, candidates: list[Candidate]) -> tuple[Candidate | None, list[Candidate]]:
    """`(resolved, matches)`: `resolved` when exactly one live candidate clears the threshold;
    `matches` are all that did, for Jev when there are several."""
    if not needs_target(edge):
        return None, []
    descriptor = edge.get("descriptor") or {}
    fitting = target_candidates(edge, candidates)
    matches = [c for c in fitting if descriptor_score(descriptor, c) >= DESCRIPTOR_THRESHOLD]
    return (matches[0] if len(matches) == 1 else None), matches


# ---- straight line ------------------------------------------------------------------------------


def edge_tier(edge: dict) -> str | None:
    """The pinned per-edge tier; None on graphs compiled before tiers existed (policy alone gates)."""
    tier = edge.get("tier")
    return tier if tier in TIERS else None


def edge_class(edge: dict) -> str:
    return str(edge.get("irreversibility") or ("committing" if edge["action_class"] == "submit" else "mutating"))


def slot_is_linked(slot: str | None, inputs: dict[str, dict], graph: Graph) -> bool:
    """Declared (a bound input) or transfer-linked (`fact:` slot read by an earlier edge)."""
    if not slot:
        return True
    if slot in inputs:
        return True
    return slot.startswith("fact:") and bool(graph.producers(slot))


def straight_line(edge: dict, inputs: dict[str, dict], graph: Graph) -> bool:
    if edge_tier(edge) != "unattended" or edge["policy"] != "auto":
        return False
    cls = edge_class(edge)
    if cls == "navigational":
        return True
    return cls == "mutating" and slot_is_linked(edge.get("slot"), inputs, graph)


# ---- recovery -----------------------------------------------------------------------------------


def _words(c: Candidate) -> set[str]:
    return set(c.name.lower().replace("{", " ").replace("}", " ").split())


def dismiss_control(observation: Observation) -> Candidate | None:
    """A cancel/close/dismiss button on screen, never an affirmative one."""
    for c in observation.candidates:
        if c.role not in ("button", "interactive") and c.kind != "interactive":
            continue
        words = _words(c)
        if words & DISMISS_WORDS and not words & AFFIRMATIVE_WORDS:
            return c
    return None


def modal_is_dismissable(observation: Observation) -> bool:
    """The recovery fragment's precondition: a modal with no textbox and no commit-vocabulary
    primary button."""
    l1 = observation.facts.get("l1") or {}
    if not l1.get("modal") and not any(t.startswith("ctx:") for t in observed_l0(observation)):
        return False
    if any(c.role in ("textbox", "combobox", "searchbox") or c.kind == "field" for c in observation.candidates):
        return False
    primary = l1.get("primary") or {}
    primary_words = set(str(primary.get("name") or "").lower().split())
    if primary_words & AFFIRMATIVE_WORDS:
        return False
    return not any(c.attrs.get("primary") and _words(c) & AFFIRMATIVE_WORDS for c in observation.candidates)


def entry_edge(graph: Graph, state: PlannerState) -> dict | None:
    """The recorded navigational edge into the node the run last believed itself in."""
    if not state.node:
        return None
    for e in sorted(graph.edges.values(), key=lambda e: (-e["stats"]["support"], e["id"])):
        if (
            e["to"] == state.node
            and e["frm"] != e["to"]
            and edge_class(e) == "navigational"
            and e["action_class"] in ("navigate", "click", "press")
        ):
            return e
    return None


# ---- questions ------------------------------------------------------------------------------------


def v3_state(
    definition: dict,
    inputs: dict[str, dict],
    state: PlannerState,
    observation: Observation,
    graph: Graph,
    nodes: list[dict],
    edges: list[dict],
    limits_left: dict,
) -> dict:
    return {
        "goal": definition["goal"],
        "success_criteria": definition["success_criteria"],
        "inputs": [_preview(n, b) for n, b in inputs.items()],
        "plan": {
            "where_i_may_be": [node_label(n) for n in nodes],
            "moves_recorded_from_here": [edge_label(e) for e in edges],
            "moves_so_far": [
                {"seq": t["seq"], "move": edge_label(graph.edges[t["edge"]])} for t in state.trajectory[-8:] if t["edge"] in graph.edges
            ],
            "holding_so_far": sorted(observed_l0(observation)),
        },
        "observation": {
            "harness": observation.harness,
            "l0": sorted(observed_l0(observation)),
            "l1": observation.facts.get("l1") or {},
            "candidates": [_candidate_view(c) for c in observation.candidates],
        },
        "facts_gathered": {k: v for k, v in list(state.facts.items())[-4:]},
        "limits_left": limits_left,
    }


def v3_questions(
    nodes: list[dict],
    edges: list[dict],
    targets: dict[str, list[Candidate]],
    values: dict,
    last_effect: list[str],
    *,
    ask_node: bool,
    ask_edge: bool,
) -> dict[str, dict]:
    questions: dict[str, dict] = {}
    if last_effect:
        questions["effect_seen"] = noul(
            "Does the current `observation` show the effect the previous move was recorded to have: "
            + "; ".join(effect_words(x) for x in last_effect)
            + "?",
            {"true": "Yes, the data or field named is now visibly there.", "false": "No, or the screen does not show it."},
        )
    if ask_node:
        questions["node"] = pick(
            "Several recorded states have exactly this L0. Which of `plan.where_i_may_be` is the current `observation`?",
            [node_label(n) for n in nodes],
            "None of these.",
        )
    if ask_edge:
        questions["edge"] = pick(
            "Given `goal`, `facts_gathered` and the current `observation`, which of `plan.moves_recorded_from_here` is the "
            "right next move? Choose the `done` option only when the goal is already met by what was done.",
            [edge_label(e) for e in edges] + ([DONE] if any(n.get("terminal") for n in nodes) else []),
            "None of these moves fits what is on screen; a person should look.",
        )
        questions["irreversible"] = noul(
            "Would the chosen move, carried out on the current `observation`, submit, send, delete, pay, sign, "
            "or otherwise commit something a person could not simply undo?",
            {
                "true": "Yes: it commits or destroys something outside this run.",
                "false": "No: it only opens, reads, selects, types into a field, or waits.",
            },
        )
    for e in edges:
        several = targets.get(e["id"]) or []
        if len(several) > 1:
            questions[target_question(e)] = pick(
                f"If the next move is “{edge_label(e)}”: several candidates fit its control. Which one is it?",
                [c.label for c in several],
                "None of these is that control.",
            )
    if values and any(e["action_class"] in VALUED and e.get("slot") not in values for e in edges):
        questions["value"] = pick(
            "If the chosen move types something, which of these declared inputs or gathered values is the one the move's slot names?",
            list(values.keys()),
            "Nothing declared fits; do not type anything.",
        )
    return questions


def _empty_judgment() -> Judgment:
    return Judgment("code", {}, 0, 0, "code")


# ---- read-back and shadow -------------------------------------------------------------------------


def criteria_of(definition: dict) -> list[dict]:
    goal = (definition.get("graph") or {}).get("goal") or {}
    return list(goal.get("criteria") or [])


def pending_read_backs(definition: dict, state: PlannerState) -> list[dict]:
    """`read_back` criteria the device has not answered yet, in declared order."""
    answered = state.recovery.get("read_back") or {}
    return [c for c in criteria_of(definition) if c.get("type") == "read_back" and c["slot"] not in answered]


def expected_value(slot: str, inputs: dict[str, dict], state: PlannerState) -> str | None:
    """The value a read-back compares against: the declared input or the gathered fact of that slot."""
    b = inputs.get(slot)
    if b and b.get("kind") == "value" and b.get("value") not in (None, ""):
        return str(b["value"])
    for facts in state.facts.values():
        if isinstance(facts, dict) and facts.get(slot) not in (None, ""):
            return str(facts[slot])
    return None


def read_back_action(
    definition: dict, graph: Graph, state: PlannerState, inputs: dict[str, dict], observation: Observation, step_id: str, seq: int
) -> tuple[Action | None, dict]:
    """On the goal frame, the next `read_back` criterion becomes a device-side comparison along
    the task's `read_back_via` edge. Criteria that cannot be checked are answered `False` at once."""
    for c in pending_read_backs(definition, state):
        slot = str(c["slot"])
        via = graph.edges.get(str(c.get("read_back_via") or ""))
        value = expected_value(slot, inputs, state)
        if via is None or edge_class(via) != "navigational" or value is None:
            state.recovery.setdefault("read_back", {})[slot] = False
            continue
        target, _ = resolve_target(via, observation.candidates)
        reads_screen = via["action_class"] in ("read", "extract")
        if needs_target(via) and target is None and not reads_screen:
            state.recovery.setdefault("read_back", {})[slot] = False
            continue
        state.recovery["read_back_pending"] = slot
        primitive = via["action_class"] if via["action_class"] in ("read", "extract", "click", "navigate") else "extract"
        args = {"read_back": slot, "delay_ms": int(c.get("read_back_delay") or 0) * 1000}
        return Action(step_id, seq, primitive, target=target, value_input=slot, value=value, args=args), {"slot": slot, "via": via["id"]}
    return None, {}


def record_read_back(state: PlannerState, facts: dict) -> None:
    """The device's answer to a pending read-back: a boolean per slot, never the text."""
    slot = state.recovery.pop("read_back_pending", None)
    answer = facts.get("read_back")
    if slot is None or not isinstance(answer, dict):
        return
    state.recovery.setdefault("read_back", {})[slot] = bool(answer.get("ok"))


def score_shadow(state: PlannerState, located: list[dict], graph: Graph, l0: frozenset[str]) -> bool | None:
    """In `shadow` the recorder proposes and the employee acts: once the screen leaves the frame the
    proposal was made on, the located node is compared with the proposed edge's destination."""
    pending = state.recovery.get("shadow_pending")
    if not pending:
        return None
    if frozenset(pending["l0"]) == l0:
        pending["waited"] = int(pending.get("waited", 0)) + 1
        if pending["waited"] < SHADOW_MAX_WAITS:
            return None
        acted = None
    else:
        acted = located[0]["key"] if len(located) == 1 else None
    edge = graph.edges.get(pending["edge"], {})
    agree = acted is not None and acted == edge.get("to")
    state.recovery.pop("shadow_pending", None)
    state.recovery.setdefault("shadow", []).append({"edge": pending["edge"], "from": pending["from"], "acted": acted, "agree": agree})
    return agree


# ---- the step -------------------------------------------------------------------------------------


def plan_v3_step(
    state: PlannerState,
    definition: dict,
    inputs: dict[str, dict],
    observation: Observation,
    *,
    run_id: str,
    primitives: set[str],
    limits_left: dict,
    mode: str = "sandbox",
    risk_threshold: float = DEFAULT_RISK_THRESHOLD,
    judge_fn: JudgeFn,
) -> tuple[Decision, Judgment, dict]:
    graph = Graph.from_definition(definition)
    assert graph is not None, "plan_v3_step needs a definition with a v3 graph"
    seq = state.n + 1
    step_id = step_id_for(run_id, seq)
    l0 = observed_l0(observation)
    last = state.trajectory[-1] if state.trajectory else None
    last_edge = graph.edges.get(last["edge"], {}) if last else {}
    previous_l0 = frozenset(state.recovery.get("l0") or [])
    state.recovery["l0"] = sorted(l0)

    # effect_seen: L0 diff first.
    last_effect: list[str] = []
    if last and last.get("effect_seen") is None:
        effect = list(last_edge.get("effect", []))
        if set(effect) <= l0:
            last["effect_seen"] = True
            last["effect_by"] = "l0"
        elif previous_l0 and previous_l0 != l0 and edge_class(last_edge) == "navigational":
            last["effect_seen"] = True
            last["effect_by"] = "l0_changed"
        else:
            last_effect = effect

    located = locate(graph, l0)
    shadow_agree = score_shadow(state, located, graph, l0)
    detail: dict = {
        "step_id": step_id,
        "seq": seq,
        "phase": "plan",
        "planner": "graph_v3",
        "rejudged": int(state.recovery.get("rejudged", 0)),
        "recoveries": int(state.recovery.get("used", 0)),
        "located": len(located),
        "node": located[0]["key"] if len(located) == 1 else None,
        "p_node": 1.0 if len(located) == 1 else 0.0,
        "edge": None,
        "next_action": NONE,
        "p_action": None,
        "policy": None,
        "tier": None,
        "irreversibility": None,
        "target": None,
        "target_by": None,
        "p_target": None,
        "p_irreversible": 0.0,
        "effect_seen": last.get("effect_seen") if last else None,
        "straight_line": False,
        "shadow_agree": shadow_agree,
    }
    judgment = _empty_judgment()

    def pause(reason: str, description: str, target: Candidate | None = None) -> Pause:
        return Pause(
            reason,
            step_id,
            {
                "step_id": step_id,
                "seq": seq,
                "harness": observation.harness,
                "action": detail["next_action"],
                "description": description,
                "target": {"label": target.label, "role": target.role, "id": target.id} if target else None,
                "risk": {"irreversible": round(float(detail["p_irreversible"]), 3)},
                "candidates": [{"id": c.id, "label": c.label, "role": c.role, "p": None} for c in observation.candidates[:12]],
                "chosen": target.id if target else None,
                "value_from": None,
                "reason": reason,
                "node": detail["node"],
                "edge": detail["edge"],
                "policy": detail["policy"],
            },
        )

    def recover(kind: str, action: Action) -> Decision:
        used = int(state.recovery.get("used", 0))
        if used >= RECOVERY_BUDGET:
            return pause("off_plan", f"Recovery budget spent ({used}); the screen is not a recorded state.")
        state.recovery["used"] = used + 1
        state.recovery.setdefault("log", []).append(
            {"seq": seq, "kind": kind, "primitive": action.primitive, "edge": last["edge"] if last else None}
        )
        detail["recovery"] = kind
        detail["next_action"] = action.primitive
        return Act(action)

    # Sensitive windows always pause; no recovery touches them.
    if observation.facts.get("sensitive"):
        return pause("sensitive", "A sign-in, session, payment or private window is in front."), judgment, detail

    # in:<unknown> → recovery, never a guess.
    if not located:
        if state.recovery.get("reobserved_at") != seq - 1 and observation.facts.get("settled") is False:
            state.recovery["reobserved_at"] = seq
            detail["next_action"] = "wait"
            return Act(Action(step_id, seq, "wait", args={"ms": 1500})), judgment, detail
        if modal_is_dismissable(observation):
            c = dismiss_control(observation)
            if c is not None:
                return recover("dismiss_modal", Action(step_id, seq, "click", target=c)), judgment, detail
            if "press" in primitives:
                return recover("dismiss_modal", Action(step_id, seq, "press", args={"key": "Escape"})), judgment, detail
        entry = entry_edge(graph, state)
        if entry is not None and entry["action_class"] in primitives:
            target, _ = resolve_target(entry, observation.candidates)
            if entry["action_class"] == "navigate":
                urls = [str(b["value"]) for b in inputs.values() if b.get("kind") == "value" and str(b.get("value", "")).startswith("http")]
                action = Action(step_id, seq, "navigate", args={"url": urls[0]} if urls else {"name": entry.get("control") or ""})
                return recover("entry_edge", action), judgment, detail
            if target is not None:
                return recover("entry_edge", Action(step_id, seq, entry["action_class"], target=target)), judgment, detail
        return pause("off_plan", "The screen's L0 matches no recorded state."), judgment, detail

    # Tie on L0 (graph defect): Jev picks among the tied nodes only.
    node: dict | None = located[0] if len(located) == 1 else None
    edges_for = lambda n: [e for e in graph.outgoing(n["key"]) if e["action_class"] in primitives][:MAX_EDGE_OFFERS]  # noqa: E731
    values = value_candidates(inputs, state.facts)
    if node is None:
        judgment = judge_fn(
            v3_state(definition, inputs, state, observation, graph, located, [], limits_left),
            v3_questions(located, [], {}, values, last_effect, ask_node=True, ask_edge=False),
        )
        choice, p_node = judgment.choice("node")
        node = next((n for n in located if node_label(n) == choice), None) if choice != NONE else None
        detail["p_node"] = round(p_node, 3)
        if node is None or p_node < NODE_CONFIDENCE:
            return pause("off_plan", "Several recorded states share this L0 and none was recognised."), judgment, detail
        detail["node"] = node["key"]
        if last_effect:
            p = judgment.noul("effect_seen")
            last["effect_seen"] = p >= EFFECT_SEEN
            last["p_effect"] = round(p, 3)
            last_effect = []
    state.node = node["key"]
    offered = edges_for(node)

    # Goal frame: read-backs are checked in code before anyone is asked whether the task is done.
    if node.get("terminal") and pending_read_backs(definition, state):
        rb, rb_detail = read_back_action(definition, graph, state, inputs, observation, step_id, seq)
        if rb is not None:
            detail.update({"next_action": rb.primitive, "read_back": rb_detail})
            state.trajectory.append({"edge": rb_detail["via"], "step_id": step_id, "seq": seq, "effect_seen": True, "read_back": True})
            return Act(rb), judgment, detail

    # Targets in code, for every offered edge.
    resolved: dict[str, Candidate | None] = {}
    several: dict[str, list[Candidate]] = {}
    for e in offered:
        r, m = resolve_target(e, observation.candidates)
        resolved[e["id"]] = r
        several[e["id"]] = m

    # Straight line: exactly one candidate edge that the tier and class allow, target already known.
    live = [e for e in offered if not needs_target(e) or resolved[e["id"]] is not None]
    edge: dict | None = None
    p_edge = 1.0
    p_irreversible = 0.0
    edge_choice = ""
    if not last_effect and len(live) == 1 and len(offered) == 1 and straight_line(live[0], inputs, graph) and not node.get("terminal"):
        edge = live[0]
        detail["straight_line"] = True
    else:
        judgment = judge_fn(
            v3_state(definition, inputs, state, observation, graph, [node], offered, limits_left),
            v3_questions([node], offered, several, values, last_effect, ask_node=False, ask_edge=True),
        )
        if last_effect:
            p = judgment.noul("effect_seen")
            last["effect_seen"] = p >= EFFECT_SEEN
            last["p_effect"] = round(p, 3)
            detail["effect_seen"] = last["effect_seen"]
        edge_choice, p_edge = judgment.choice("edge")
        edge = next((e for e in offered if edge_label(e) == edge_choice), None) if edge_choice not in (NONE, DONE) else None
        p_irreversible = judgment.noul("irreversible")
        if edge is not None and edge["frm"] != node["key"]:
            # Cannot happen: only the located node's edges were offered. Counted, never acted on.
            state.recovery["rejudged"] = int(state.recovery.get("rejudged", 0)) + 1
            detail["rejudged"] = state.recovery["rejudged"]
            return pause("off_plan", "The chosen move does not start from the located state."), judgment, detail

    detail.update(
        {
            "edge": edge["id"] if edge else None,
            "next_action": edge["action_class"] if edge else ("done" if edge_choice == DONE else NONE),
            "p_action": round(p_edge, 3),
            "policy": edge["policy"] if edge else None,
            "tier": edge_tier(edge) if edge else None,
            "p_irreversible": round(p_irreversible, 3),
        }
    )

    if edge_choice == DONE and p_edge >= ACTION_CONFIDENCE:
        if not node.get("terminal"):
            return pause("off_plan", "`done` was chosen before the recorded goal frame."), judgment, detail
        state.consecutive_none = 0
        return Finish("done"), judgment, detail
    if edge is None or p_edge < ACTION_CONFIDENCE:
        state.consecutive_none += 1
        if not offered:
            return pause("off_plan", "No recorded move leaves this state."), judgment, detail
        if state.recovery.get("reobserved_at") != seq - 1:
            state.recovery["reobserved_at"] = seq
            detail["next_action"] = "wait"
            return Act(Action(step_id, seq, "wait", args={"ms": 1500})), judgment, detail
        return pause("off_plan", "No recorded move from this state fits what is on screen."), judgment, detail
    state.consecutive_none = 0

    primitive = edge["action_class"]
    cls = raise_irreversibility(edge_class(edge), "committing" if p_irreversible >= risk_threshold else None)
    detail["irreversibility"] = cls

    target: Candidate | None = None
    if needs_target(edge):
        target = resolved[edge["id"]]
        detail["target_by"] = "code" if target else None
        if target is None and len(several[edge["id"]]) > 1 and target_question(edge) in judgment.answers:
            label, p_target = judgment.choice(target_question(edge))
            target = next((c for c in several[edge["id"]] if c.label == label), None) if label != NONE else None
            detail["p_target"] = round(p_target, 3)
            detail["target_by"] = "jev" if target and p_target >= TARGET_CONFIDENCE else None
            if target is not None and p_target < TARGET_CONFIDENCE:
                target = None
        if target is None:
            if state.recovery.get("reobserved_at") != seq - 1:
                state.recovery["reobserved_at"] = seq
                detail["next_action"] = "wait"
                return Act(Action(step_id, seq, "wait", args={"ms": 1500})), judgment, detail
            what = (edge.get("descriptor") or {}).get("name") or edge.get("control") or primitive
            return pause("ambiguous_target", f"No live candidate on screen is clearly “{what}”."), judgment, detail
        detail["target"] = target.label

    value_input = value = None
    if primitive in VALUED:
        judged_value = None
        p_value = None
        if "value" in judgment.answers:
            label, p_value = judgment.choice("value")
            if label != NONE and label in values:
                judged_value = values[label]
        chosen = resolve_value(edge, inputs, state, graph, judged_value)
        if chosen is None or (edge.get("slot") not in inputs and (p_value or 0) < VALUE_CONFIDENCE):
            return pause("missing_value", f"Nothing declared or gathered fills “{edge.get('slot')}”."), judgment, detail
        value_input, value = chosen
    detail["value_input"] = value_input

    args: dict = {}
    if primitive == "navigate":
        urls = [
            str(b["value"])
            for b in inputs.values()
            if b.get("kind") == "value" and str(b.get("value", "")).startswith(("http://", "https://"))
        ]
        args = {"url": urls[0]} if urls else {"name": edge.get("control") or ""}
    elif primitive == "press" or (primitive == "submit" and (edge.get("descriptor") or {}).get("role") == "key"):
        args = {"key": (edge.get("descriptor") or {}).get("name") or edge.get("control") or "Enter"}
    elif primitive == "wait":
        args = {"ms": 1500}
    action = Action(
        step_id, seq, primitive, target=target if primitive in TARGETED else None, value_input=value_input, value=value, args=args
    )

    if mode == "dry_run" and (primitive in WRITE_PRIMITIVES or cls != "navigational"):
        return Finish("dry_run_stopped_before_write"), judgment, detail
    decided = state.decisions.get(step_id, {}).get("decision")
    if decided == "deny":
        return Stop("denied"), judgment, detail
    committing = cls == "committing" or primitive in ALWAYS_GATED
    if edge_tier(edge) == "shadow" and mode != "dry_run":
        # Shadow: the recorder proposes, the employee acts; nothing is performed and nobody is paused.
        if not any(p["step_id"] == step_id for p in state.proposals):
            state.proposals.append({"edge": edge["id"], "step_id": step_id, "shadow": True})
        state.recovery["shadow_pending"] = {"edge": edge["id"], "from": node["key"], "l0": sorted(l0), "seq": seq}
        detail["shadow"] = True
        detail["next_action"] = "wait"
        return Act(Action(step_id, seq, "wait", args={"ms": 3000})), judgment, detail
    gated = committing or edge["policy"] != "auto" or p_irreversible >= risk_threshold or edge_tier(edge) == "ask"
    if gated and decided != "approve":
        if not any(p["step_id"] == step_id for p in state.proposals):
            state.proposals.append({"edge": edge["id"], "step_id": step_id})
        reason = "irreversible" if committing or p_irreversible >= risk_threshold else "confirm"
        return pause(reason, action.describe(), target), judgment, detail
    state.trajectory.append({"edge": edge["id"], "step_id": step_id, "seq": seq, "effect_seen": None if edge.get("effect") else True})
    state.node = edge["to"]
    return Act(action, gated=decided == "approve"), judgment, detail
