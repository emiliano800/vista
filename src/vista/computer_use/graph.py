"""The planner over a `PlanGraph`: Jev chooses among the moves the recordings observed.

When the approved definition carries a graph, a step is no longer "which primitive, which
control" over the whole vocabulary. Code locates the run on the graph (which state it is in),
enumerates that state's outgoing edges, and Jev picks one — or `done`, or none of them. The
edge fixes the primitive, the control it was recorded on and the slot it types from; the
observation still supplies the concrete target, and typed values still come only from declared
inputs or facts a previous edge produced. Absence is defined, not judged: a state with no
observed edge, or a move Jev does not recognise on screen, pauses for a person.

Every traversal is remembered in the checkpoint as a trajectory; `merge_run` turns it into a
graph delta (executed / verified_ok / effect_missing / approved / denied per edge, provenance
`run:<id>`) that a *draft* may absorb. The approved version's graph is never touched here.
"""

from __future__ import annotations

from dataclasses import dataclass

from taskmining.state import app_role, empty_stats, jaccard
from vista.agents.jev import NONE, Judgment, noul, pick
from vista.computer_use.harness import ALWAYS_GATED, TARGETED, VALUED, WRITE_PRIMITIVES, Action, Candidate, Observation
from vista.computer_use.planner import (
    ACTION_CONFIDENCE,
    DEFAULT_RISK_THRESHOLD,
    MAX_CONSECUTIVE_NONE,
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

EFFECT_SEEN = 0.5  # p(effect visible) below which the edge counts as effect_missing
NODE_CONFIDENCE = 0.5
DONE = "done: the goal is met; stop and verify"
MAX_NODE_OFFERS = 6
MAX_EDGE_OFFERS = 12
_DOC_ROLE = {".pdf": "pdf", ".csv": "spreadsheet", ".xlsx": "spreadsheet", ".xls": "spreadsheet"}


@dataclass(frozen=True)
class Graph:
    nodes: dict[str, dict]
    edges: dict[str, dict]
    start: list[str]

    @classmethod
    def from_definition(cls, definition: dict) -> Graph | None:
        g = definition.get("graph")
        if not g:
            return None
        return cls({n["key"]: n for n in g["nodes"]}, {e["id"]: e for e in g["edges"]}, list(g["start"]))

    def outgoing(self, key: str) -> list[dict]:
        return sorted((e for e in self.edges.values() if e["frm"] == key), key=lambda e: (-e["stats"]["support"], e["id"]))

    def producers(self, slot: str) -> set[str]:
        return {e["id"] for e in self.edges.values() if slot in e.get("produces", [])}


# ---- locating the run on the graph ----------------------------------------------------------------


def observed_role(observation: Observation) -> str:
    """The app role code reads off the observation — the same function the recorder used."""
    facts = observation.facts
    if observation.harness in ("documents", "local"):
        names = [c.name for c in observation.candidates if c.kind == "document"]
        roles = {_DOC_ROLE.get("." + n.rsplit(".", 1)[-1].lower().rstrip(")"), "documents") for n in names if "." in n}
        if len(roles) == 1:
            return roles.pop()
        if observation.harness == "local" and not names:
            local = {c.attrs.get("harness") for c in observation.candidates}
            return "browser" if local == {"http"} else "workspace" if local == {"workspace"} else "documents"
        return "documents"
    if observation.harness == "http":
        return "browser"
    if observation.harness == "workspace":
        return "workspace"
    role = app_role(str(facts.get("app", "")), str(facts.get("title", "")) + " " + str(facts.get("url", "")))
    return role if role != "other" else observation.harness


def held(state: PlannerState, graph: Graph) -> set[str]:
    """Signature tokens the trajectory has produced so far (effects of edges whose effect was seen)."""
    tokens: set[str] = set()
    for t in state.trajectory:
        if t.get("effect_seen") is False:
            continue
        tokens.update(graph.edges.get(t["edge"], {}).get("effect", []))
    return tokens


def node_offers(state: PlannerState, graph: Graph, observation: Observation) -> list[dict]:
    """Where the run may be, ranked in code: the last move's destination first, then its origin,
    then start states of the observed role, then any state of that role near what is held."""
    role = observed_role(observation)
    seen: list[dict] = []

    def add(key: str | None) -> None:
        n = graph.nodes.get(key or "")
        if n and n not in seen and n["app_role"] == role:
            seen.append(n)

    if state.trajectory:
        last = graph.edges.get(state.trajectory[-1]["edge"], {})
        add(last.get("to"))
        add(last.get("frm"))
    if state.node:
        add(state.node)
    if not state.trajectory:
        for key in graph.start:
            add(key)
    have = held(state, graph)
    for n in sorted(graph.nodes.values(), key=lambda n: (-jaccard(n["signature"], have), n["key"])):
        if len(seen) >= MAX_NODE_OFFERS:
            break
        add(n["key"])
    return seen[:MAX_NODE_OFFERS]


def node_label(n: dict) -> str:
    holding = ", ".join(n["signature"]) or "nothing yet"
    return f"{n['app_role']} · {n['activity']} · holding {holding}" + (" · end" if n.get("terminal") else "")


# Candidate kinds a control of this action class can be. The target is judged *per offered edge*
# ("which candidate is the control this move was recorded on"), over these kinds only, so the
# edge Jev picks and the target it picks can never disagree about what is being acted on.
TARGET_KINDS = {
    "type_value": {"field"},
    "click": {"interactive", "link", "row"},
    "submit": {"interactive", "link", "row"},
}


def target_question(e: dict) -> str:
    return f"target:{e['id']}"


def target_candidates(e: dict, candidates: list[Candidate]) -> list[Candidate]:
    kinds = TARGET_KINDS.get(e["action_class"])
    return [c for c in candidates if kinds is None or c.kind in kinds]


def target_for(edge: dict | None, judgment: Judgment, candidates: list[Candidate]) -> tuple[Candidate | None, float | None]:
    if edge is None or edge["action_class"] not in TARGETED or target_question(edge) not in judgment.answers:
        return None, None
    label, p = judgment.choice(target_question(edge))
    by_label = {c.label: c for c in target_candidates(edge, candidates)}
    return (by_label.get(label), p) if label != NONE else (None, p)


def edge_label(e: dict) -> str:
    what = e["action_class"].replace("_", " ")
    control = f" “{e['control']}”" if e.get("control") else ""
    slot = f" with {e['slot']}" if e.get("slot") else ""
    return f"{what}{control}{slot} → {', '.join(e.get('effect', [])) or 'no new data'}"


# Signature entries are `kind:name` labels; spelled out for Jev so "field:CLIENT_NAME" reads as
# the visible thing it stands for (the graph itself keeps the labels).
EFFECT_WORDS = {
    "field": "the field for “{name}” now holds a value (a candidate with has_value, or the filtered/updated data it drives)",
    "rec": "a “{name}” record is open or located on screen",
    "dialog": "the “{name}” screen, module or dialog is open",
    "doc": "the document “{name}” is open",
    "fact": "“{name}” has been read off the screen",
    "msg": "the message “{name}” is open",
}


def effect_words(label: str) -> str:
    kind, _, name = label.partition(":")
    template = EFFECT_WORDS.get(kind)
    return template.format(name=name) if template and name else label


# ---- questions ----------------------------------------------------------------------------------


def graph_state(
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
            "holding_so_far": sorted(held(state, graph)),
        },
        "observation": {
            "harness": observation.harness,
            "facts": observation.facts,
            "candidates": [_candidate_view(c) for c in observation.candidates],
        },
        "facts_gathered": {k: v for k, v in list(state.facts.items())[-4:]},
        "limits_left": limits_left,
    }


def graph_questions(
    nodes: list[dict], edges: list[dict], observation: Observation, values: dict, last_effect: list[str]
) -> dict[str, dict]:
    questions: dict[str, dict] = {}
    if last_effect:
        questions["effect_seen"] = noul(
            "Does the current `observation` show the effect the previous move was recorded to have: "
            + "; ".join(effect_words(x) for x in last_effect)
            + "?",
            {"true": "Yes, the data or field named is now visibly there.", "false": "No, or the screen does not show it."},
        )
    questions["node"] = pick(
        "Which of `plan.where_i_may_be` describes the current `observation` — same kind of application, same things already held?",
        [node_label(n) for n in nodes],
        "None of these; the run is somewhere the recordings never were.",
    )
    questions["edge"] = pick(
        "Given `goal`, `facts_gathered` and the current `observation`, which of `plan.moves_recorded_from_here` is the right next move? "
        "Choose the `done` option only when the goal is already met by what was done.",
        [edge_label(e) for e in edges] + [DONE],
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
        fitting = target_candidates(e, observation.candidates)
        if e["action_class"] in TARGETED and fitting:
            control = f"the control “{e['control']}”" if e.get("control") else "the control"
            slot = f" (it types the value named {e['slot']})" if e.get("slot") else ""
            questions[target_question(e)] = pick(
                f"If the next move is “{edge_label(e)}”: which candidate in `observation.candidates` is {control} that move "
                f"was recorded on{slot}? Match by meaning — a “Save” button may be labelled “Post”, a supplier field “Vendor” — "
                "but a field for one thing is never the field for another.",
                [c.label for c in fitting],
                "None of these is that control.",
            )
    if values:
        questions["value"] = pick(
            "If the chosen move types something, which of these declared inputs or gathered values is the one the move's slot names?",
            list(values.keys()),
            "Nothing declared fits; do not type anything.",
        )
    return questions


# ---- the step -----------------------------------------------------------------------------------


def resolve_value(
    edge: dict, inputs: dict[str, dict], state: PlannerState, graph: Graph, judged: tuple[str, str] | None
) -> tuple[str, str] | None:
    """The slot decides: a declared input is resolved by code without asking; a produced fact may
    only be typed from the facts of a step that traversed an edge producing that slot."""
    slot = edge.get("slot")
    if not slot:
        return judged
    binding = inputs.get(slot)
    if binding and binding.get("kind") == "value" and binding.get("value") not in (None, ""):
        return slot, str(binding["value"])
    if judged is None:
        return None
    producing_steps = {f"step {t['seq']}" for t in state.trajectory if t["edge"] in graph.producers(slot)}
    source = judged[0].split(":", 1)[0]
    return judged if source in producing_steps else None


def plan_graph_step(
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
    assert graph is not None, "plan_graph_step needs a definition with a graph"
    seq = state.n + 1
    step_id = step_id_for(run_id, seq)
    nodes = node_offers(state, graph, observation)
    edges_by_node = {n["key"]: [e for e in graph.outgoing(n["key"]) if e["action_class"] in primitives][:MAX_EDGE_OFFERS] for n in nodes}
    offered: list[dict] = []
    for n in nodes:
        for e in edges_by_node[n["key"]]:
            if e not in offered:
                offered.append(e)
    offered = offered[:MAX_EDGE_OFFERS]
    values = value_candidates(inputs, state.facts)
    last = state.trajectory[-1] if state.trajectory else None
    last_effect = list(graph.edges.get(last["edge"], {}).get("effect", [])) if last and last.get("effect_seen") is None else []
    questions = graph_questions(nodes, offered, observation, values, last_effect)
    judgment = judge_fn(graph_state(definition, inputs, state, observation, graph, nodes, offered, limits_left), questions)

    if last_effect:
        p_effect = judgment.noul("effect_seen")
        last["effect_seen"] = p_effect >= EFFECT_SEEN
        last["p_effect"] = round(p_effect, 3)
    node_choice, p_node = judgment.choice("node")
    node = next((n for n in nodes if node_label(n) == node_choice), None) if node_choice != NONE else None
    edge_choice, p_edge = judgment.choice("edge")
    matching = [e for e in offered if edge_label(e) == edge_choice] if edge_choice not in (NONE, DONE) else []
    edge = next((e for e in matching if node and e["frm"] == node["key"]), matching[0] if matching else None)
    rejudged = False
    if node and edge and edge["frm"] != node["key"] and edges_by_node[node["key"]]:
        # Jev located the run on one state but chose a move recorded from another (typically a
        # later one whose control happens to be on screen). Ask again with only the moves
        # recorded from the located state — the graph decides what is offered, Jev only ranks.
        rejudged = True
        offered = edges_by_node[node["key"]]
        second = judge_fn(
            graph_state(definition, inputs, state, observation, graph, [node], offered, limits_left),
            graph_questions([node], offered, observation, values, []),
        )
        judgment = Judgment(
            second.model,
            {**{k: v for k, v in judgment.answers.items() if k == "effect_seen"}, **second.answers},
            judgment.input_tokens + second.input_tokens,
            judgment.output_tokens + second.output_tokens,
            second.source,
        )
        edge_choice, p_edge = judgment.choice("edge")
        edge = next((e for e in offered if edge_label(e) == edge_choice), None) if edge_choice not in (NONE, DONE) else None
    p_irreversible = judgment.noul("irreversible")
    target, p_target = target_for(edge, judgment, observation.candidates)
    judged_value = None
    p_value = None
    if "value" in questions:
        label, p_value = judgment.choice("value")
        if label != NONE and label in values:
            judged_value = values[label]

    detail = {
        "step_id": step_id,
        "seq": seq,
        "phase": "plan",
        "planner": "graph",
        "rejudged": rejudged,
        "node": node["key"] if node else None,
        "p_node": round(p_node, 3),
        "edge": edge["id"] if edge else None,
        "next_action": edge["action_class"] if edge else ("done" if edge_choice == DONE else NONE),
        "p_action": round(p_edge, 3),
        "policy": edge["policy"] if edge else None,
        "target": target.label if target else None,
        "p_target": round(p_target, 3) if p_target is not None else None,
        "p_irreversible": round(p_irreversible, 3),
        "effect_seen": last.get("effect_seen") if last_effect and last else None,
    }
    p_targets = judgment.probabilities(target_question(edge)) if edge and target_question(edge) in judgment.answers else {}
    candidates_out = [
        {"id": c.id, "label": c.label, "role": c.role, "p": round(p_targets.get(c.label, 0.0), 3) if p_targets else None}
        for c in observation.candidates[:12]
    ]

    def pause(reason: str, description: str) -> Pause:
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
                "risk": {"irreversible": round(p_irreversible, 3)},
                "candidates": candidates_out,
                "chosen": target.id if target else None,
                "value_from": None,
                "reason": reason,
                "node": detail["node"],
                "edge": detail["edge"],
                "policy": detail["policy"],
            },
        )

    # --- gates, in order ---
    if node is None or p_node < NODE_CONFIDENCE:
        state.consecutive_none += 1
        if state.consecutive_none >= MAX_CONSECUTIVE_NONE or not nodes:
            return pause("off_plan", "The screen matches no state the recordings passed through."), judgment, detail
        return Act(Action(step_id, seq, "wait", args={"ms": 1500})), judgment, detail
    state.node = node["key"]
    if edge_choice == DONE and p_edge >= ACTION_CONFIDENCE:
        state.consecutive_none = 0
        return Finish("done"), judgment, detail
    if edge is None or p_edge < ACTION_CONFIDENCE:
        state.consecutive_none += 1
        if state.consecutive_none >= MAX_CONSECUTIVE_NONE or not edges_by_node[node["key"]]:
            return pause("off_plan", "No recorded move from this state fits what is on screen."), judgment, detail
        return Act(Action(step_id, seq, "wait", args={"ms": 1500})), judgment, detail
    state.consecutive_none = 0
    if edge["frm"] != node["key"]:
        return pause("off_plan", "The chosen move does not start from the state the run is in."), judgment, detail
    primitive = edge["action_class"]
    if primitive in TARGETED and (target is None or (p_target or 0) < TARGET_CONFIDENCE):
        return pause("ambiguous_target", f"No candidate on screen is clearly “{edge.get('control') or primitive}”."), judgment, detail
    value_input = value = None
    if primitive in VALUED:
        resolved = resolve_value(edge, inputs, state, graph, judged_value)
        if resolved is None or (edge.get("slot") not in inputs and (p_value or 0) < VALUE_CONFIDENCE):
            return pause("missing_value", f"Nothing declared or gathered fills “{edge.get('slot')}”."), judgment, detail
        value_input, value = resolved
    detail["value_input"] = value_input
    args: dict = {}
    if primitive == "navigate":
        urls = [
            str(b["value"])
            for b in inputs.values()
            if b.get("kind") == "value" and str(b.get("value", "")).startswith(("http://", "https://"))
        ]
        url = urls[0] if urls else None
        args = {"url": url} if url else {"name": edge.get("control") or ""}
    elif primitive == "press":
        args = {"key": edge.get("control") or "Enter"}
    elif primitive == "wait":
        args = {"ms": 1500}

    action = Action(
        step_id,
        seq,
        primitive,
        target=target if primitive in TARGETED else None,
        value_input=value_input,
        value=value,
        args=args,
    )
    if mode == "dry_run" and primitive in WRITE_PRIMITIVES:
        return Finish("dry_run_stopped_before_write"), judgment, detail
    decided = state.decisions.get(step_id, {}).get("decision")
    if decided == "deny":
        return Stop("denied"), judgment, detail
    gated = primitive in ALWAYS_GATED or edge["policy"] != "auto" or p_irreversible >= risk_threshold
    if gated and decided != "approve":
        if not any(p["step_id"] == step_id for p in state.proposals):
            state.proposals.append({"edge": edge["id"], "step_id": step_id})
        reason = "irreversible" if primitive in ALWAYS_GATED or p_irreversible >= risk_threshold else "confirm"
        return pause(reason, action.describe()), judgment, detail
    state.trajectory.append({"edge": edge["id"], "step_id": step_id, "seq": seq, "effect_seen": None if edge.get("effect") else True})
    state.node = edge["to"]
    return Act(action, gated=decided == "approve"), judgment, detail


# ---- what a run gives back to the graph ------------------------------------------------------------


def merge_run(definition: dict, state: PlannerState, run_id: str, verified: bool | None) -> dict | None:
    """The run as a graph delta: only the edges it traversed, with this run's counts and
    provenance, in the same shape as a recorded graph so `merge_graphs` can fold it into a draft."""
    graph = Graph.from_definition(definition)
    if graph is None or not (state.trajectory or state.proposals):
        return None
    edges: dict[str, dict] = {}

    def touch(edge_id: str, step_id: str) -> dict:
        cur = edges.setdefault(
            edge_id,
            {
                **graph.edges[edge_id],
                "stats": empty_stats(),
                "provenance": [{"source": "run", "id": run_id, "event_ids": []}],
            },
        )
        if step_id not in cur["provenance"][0]["event_ids"] and len(cur["provenance"][0]["event_ids"]) < 50:
            cur["provenance"][0]["event_ids"].append(step_id)
        return cur["stats"]

    for t in state.trajectory:
        if not state.executed(t["step_id"]):
            continue
        s = touch(t["edge"], t["step_id"])
        s["support"] += 1
        s["executed"] += 1
        s["verified_ok"] += 1 if verified else 0
        s["effect_missing"] += 1 if t.get("effect_seen") is False else 0
    for p in state.proposals:
        decision = state.decisions.get(p["step_id"], {}).get("decision")
        if decision in ("approve", "deny"):
            touch(p["edge"], p["step_id"])["approved" if decision == "approve" else "denied"] += 1
    if not edges:
        return None
    keys = {e["frm"] for e in edges.values()} | {e["to"] for e in edges.values()}
    first = graph.edges[(state.trajectory or state.proposals)[0]["edge"]]["frm"]
    return {
        "start": [first],
        "nodes": [graph.nodes[k] for k in sorted(keys)],
        "edges": [edges[k] for k in sorted(edges)],
        "trajectories": 1,
        "truncated": False,
        "compiled_by": "recorder-plan/1",
    }
