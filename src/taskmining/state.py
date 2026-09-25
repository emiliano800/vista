"""Workflow state abstraction and plan-graph algebra, shared by every producer and consumer
of a `PlanGraph`.

A recorded workflow is not a script but a graph over *frames* (v3, design of record
`docs/computer_use_system.md` §3): a node is its L0 progress set — `have:<slot>`,
`read:<slot>`, `open:<slot>`, `in:<screen-class>`, `ctx:<dialog-class>` — by *name* only;
two frames are one node iff their L0 sets are equal. L1 (landmarks, modal, primary button,
control classes) is context on the node, never identity. Graphs compiled before v3 keyed
nodes by `(app_role, activity, data_signature)`; both key functions are kept so either kind
validates. Recordings and runs are trajectories through
that graph; each traversal adds counts and provenance to an edge, never a value. The recorder
(`src/recorder/src/plan.js`) compiles trajectories with the exact same key functions below, so
a node the device produced and a node the worker observes at run time compare by key alone.
Everything here is deterministic; no model is involved.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from taskmining.tiers import lower as lower_tier

APP_ROLES: tuple[str, ...] = (
    "accounting",
    "crm",
    "spreadsheet",
    "pdf",
    "email",
    "browser",
    "documents",
    "chat",
    "workspace",
    "other",
)
# Mirrors ROLES in src/recorder/src/workflows.js (password managers never enter a graph).
_ROLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"outlook|mail|thunderbird|gmail", re.I)),
    ("accounting", re.compile(r"quickbooks|xero|sage|netsuite|odoo.*(account|invoic|bill)|freshbooks|dynamics|sap\b", re.I)),
    ("crm", re.compile(r"salesforce|hubspot|pipedrive|zoho|odoo.*(crm|pipeline|lead)|dynamics 365 sales", re.I)),
    ("spreadsheet", re.compile(r"excel|sheets|numbers|libreoffice calc", re.I)),
    ("pdf", re.compile(r"acrobat|preview|foxit|pdf", re.I)),
    ("chat", re.compile(r"slack|teams|zoom|webex", re.I)),
    ("browser", re.compile(r"chrome|safari|firefox|edge|brave|arc", re.I)),
    ("documents", re.compile(r"word|docs|pages|notepad|textedit", re.I)),
)

SIGNATURE_TOKEN = re.compile(
    r"^(?:(?:doc|rec|field|fact|dialog):[A-Za-z0-9_.-]{1,120}|msg:open"
    r"|(?:have|read|open):[A-Za-z0-9_.:-]{1,120}|in:[0-9a-f]{12,16}|ctx:[A-Za-z0-9_.-]{1,64})$"
)
L0_PREFIXES: tuple[str, ...] = ("have", "read", "open", "in", "ctx")
# Irreversibility classes, least to most. Code assigns them; Jev's `p_irreversible` may raise, never lower.
IRREVERSIBILITY: tuple[str, ...] = ("navigational", "mutating", "committing")
COMMIT_VOCAB: frozenset[str] = frozenset(
    {
        "save",
        "submit",
        "send",
        "post",
        "delete",
        "approve",
        "confirm",
        "pay",
        "ok",
        "yes",
        "continue",
        "apply",
        "done",
        "finish",
        "complete",
    }
)
_NAV_CLICK_ROLES: frozenset[str] = frozenset({"tab", "link", "row", "menuitem", "cell", "treeitem", "option"})
_NAV_ACTIONS: frozenset[str] = frozenset({"navigate", "read", "extract", "wait", "http_get"})
SLOT_METHODS: tuple[str, ...] = ("transfer", "declared", "descriptor", "storyboard")
CRITERIA_TYPES: tuple[str, ...] = ("read_back", "present", "graded")
ACTION_CLASSES: frozenset[str] = frozenset(
    {"navigate", "click", "type_value", "press", "read", "extract", "submit", "wait", "http_get", "create_task"}
)
WRITE_CLASSES: frozenset[str] = frozenset({"click", "type_value", "submit", "create_task"})
POLICIES: tuple[str, ...] = ("auto", "confirm", "always_ask")  # least to most restrictive
NEAR_JACCARD = 0.5
MAX_PROVENANCE = 50

# Promotion: how many *executed* traversals an edge needs before code may propose `auto`,
# and the verified-success rate it must hold. `submit` is never promotable in this version.
PROMOTE_MIN_EXECUTED: dict[str, int] = {
    "navigate": 3,
    "read": 3,
    "extract": 3,
    "wait": 3,
    "http_get": 3,
    "click": 5,
    "type_value": 5,
    "press": 5,
    "create_task": 5,
}
PROMOTE_MIN_SUCCESS = 0.9
PROMOTE_MAX_EFFECT_MISSING = 0.1


def app_role(app: str, title: str = "") -> str:
    s = f"{app} {title}"
    for role, pattern in _ROLE_PATTERNS:
        if pattern.search(s):
            return role
    return "other"


def _h(parts: Iterable[str]) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def state_key(role: str, activity: str, signature: Iterable[str]) -> str:
    """v1/v2 node key, kept for graphs compiled before v3."""
    return _h((role, activity, ",".join(sorted(set(signature)))))


def frame_key(l0: Iterable[str]) -> str:
    """v3 node key: the L0 set alone (`recorder/src/plan.js` frameKey)."""
    return _h(("v3", ",".join(sorted(set(l0)))))


def node_key(node: dict) -> str:
    """The key a node must carry, whichever version compiled it."""
    if node.get("l0") is not None:
        return frame_key(node["l0"])
    return state_key(node["app_role"], node["activity"], node.get("signature", ()))


def screen_class(role: str, shape: str) -> str:
    return _h(("screen", role, shape))


def irreversibility_of(action_class: str, descriptor: dict | None = None, ctx: str | None = None) -> str:
    """Twin of plan.js irreversibilityOf: from the primitive, the control descriptor and an open dialog class."""
    if action_class == "submit":
        return "committing"
    if action_class in _NAV_ACTIONS:
        return "navigational"
    name = str((descriptor or {}).get("name") or "")
    role = (descriptor or {}).get("role")
    if action_class in ("click", "press"):
        if any(w in COMMIT_VOCAB for w in name.split(" ")):
            return "committing"
        if ctx == "confirm" and action_class == "press" and re.search(r"enter|return", name):
            return "committing"
        if action_class == "click" and (role in _NAV_CLICK_ROLES or not role or role == "unknown"):
            return "navigational"
        return "mutating"
    return "mutating"


def raise_irreversibility(assigned: str, proposed: str | None) -> str:
    """Jev may only raise the class code assigned."""
    if proposed not in IRREVERSIBILITY:
        return assigned
    return IRREVERSIBILITY[max(IRREVERSIBILITY.index(assigned), IRREVERSIBILITY.index(proposed))]


def edge_id(frm: str, to: str, action_class: str, control: str | None, slot: str | None) -> str:
    return _h((frm, to, action_class, control or "", slot or ""))


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def _identity(node: dict) -> Iterable[str]:
    l0 = node.get("l0")
    return l0 if l0 is not None else node.get("signature", ())


def same_frame(node: dict, other: dict) -> bool:
    """v3 locate: two frames are one node iff their L0 sets are equal."""
    return set(_identity(node)) == set(_identity(other))


def near(node: dict, other: dict) -> bool:
    """Same role and most of the identity set in common: the tie-break candidates when no frame matches exactly."""
    return node["app_role"] == other["app_role"] and jaccard(_identity(node), _identity(other)) >= NEAR_JACCARD


def default_policy(action_class: str, irreversibility: str | None = None) -> str:
    if action_class == "submit" or irreversibility == "committing":
        return "always_ask"
    if irreversibility == "navigational":
        return "auto"
    return "confirm" if action_class in WRITE_CLASSES else "auto"


def stricter(a: str, b: str) -> str:
    return a if POLICIES.index(a) >= POLICIES.index(b) else b


def empty_stats() -> dict:
    return {"support": 0, "recorded": 0, "executed": 0, "verified_ok": 0, "approved": 0, "denied": 0, "effect_missing": 0}


def promotable(edge: dict) -> bool:
    """Whether code may *propose* `auto` for this edge. Proposals still need an admin's approval
    of the version that carries them; nothing here changes a policy by itself."""
    action = edge["action_class"]
    need = PROMOTE_MIN_EXECUTED.get(action)
    s = edge["stats"]
    if need is None or s["executed"] < need or s["denied"] > 0:
        return False
    return s["verified_ok"] / s["executed"] >= PROMOTE_MIN_SUCCESS and s["effect_missing"] / s["executed"] <= PROMOTE_MAX_EFFECT_MISSING


def _prov_key(p: dict) -> tuple[str, str]:
    return (str(p.get("source", "")), str(p.get("id", "")))


def merge_graphs(graphs: list[dict]) -> dict:
    """Union of plan graphs: nodes by key, edges by id, stats summed, provenance appended,
    the stricter policy kept. Order-independent, so the same set of trajectories always
    produces the same graph (and therefore the same `definition_hash`)."""
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}
    start: set[str] = set()
    slots: dict[str, dict] = {}
    goal: dict | None = None
    trajectories = 0
    truncated = False
    for g in graphs:
        trajectories += int(g.get("trajectories", 0))
        truncated = truncated or bool(g.get("truncated"))
        start.update(g.get("start", []))
        for s in g.get("slot_table", []):
            cur = slots.get(s["slot"])
            if cur is None:
                slots[s["slot"]] = {**s, "controls": sorted(s.get("controls", []))}
            else:
                cur["controls"] = sorted(set(cur["controls"]) | set(s.get("controls", [])))
                cur["single_recording"] = False
        if g.get("goal") and (goal is None or g["goal"]["node"] < goal["node"]):
            goal = g["goal"]
        for n in g.get("nodes", []):
            cur = nodes.get(n["key"])
            if cur is None:
                nodes[n["key"]] = {
                    **n,
                    "signature": sorted(n.get("signature", [])),
                    **({"l0": sorted(n["l0"])} if n.get("l0") is not None else {}),
                    "terminal": bool(n.get("terminal")),
                }
            else:
                cur["terminal"] = cur["terminal"] or bool(n.get("terminal"))
        for e in g.get("edges", []):
            cur = edges.get(e["id"])
            if cur is None:
                edges[e["id"]] = {
                    **e,
                    "produces": sorted(e.get("produces", [])),
                    "effect": sorted(e.get("effect", [])),
                    "stats": {**empty_stats(), **e.get("stats", {})},
                    "provenance": sorted(e.get("provenance", []), key=_prov_key)[:MAX_PROVENANCE],
                    "policy": e.get("policy") or default_policy(e["action_class"], e.get("irreversibility")),
                }
                continue
            for k in set(cur["stats"]) | set(e.get("stats", {})):
                cur["stats"][k] = int(cur["stats"].get(k, 0)) + int(e.get("stats", {}).get(k, 0))
            cur["provenance"] = sorted(cur["provenance"] + list(e.get("provenance", [])), key=_prov_key)[:MAX_PROVENANCE]
            ref = e.get("anchor_ref")
            if ref and (not cur.get("anchor_ref") or ref < cur["anchor_ref"]):
                cur["anchor_ref"] = ref
            cur["policy"] = stricter(cur["policy"], e.get("policy") or default_policy(e["action_class"], e.get("irreversibility")))
            if cur.get("irreversibility") or e.get("irreversibility"):
                cur["irreversibility"] = raise_irreversibility(cur.get("irreversibility") or "navigational", e.get("irreversibility"))
            cur["produces"] = sorted(set(cur["produces"]) | set(e.get("produces", [])))
            if cur.get("tier") or e.get("tier"):
                cur["tier"] = lower_tier(cur.get("tier"), e.get("tier"))
                since = [t for t in (cur.get("tier_since"), e.get("tier_since")) if t]
                if since:
                    cur["tier_since"] = min(since)
            cur["effect"] = sorted(set(cur["effect"]) | set(e.get("effect", [])))
    out = {
        "start": sorted(start),
        "nodes": [nodes[k] for k in sorted(nodes)],
        "edges": [edges[k] for k in sorted(edges)],
        "trajectories": trajectories,
        "truncated": truncated,
        "compiled_by": next((g.get("compiled_by") for g in graphs if g.get("compiled_by")), "recorder-plan/1"),
    }
    if slots:
        out["slot_table"] = [slots[k] for k in sorted(slots)]
    if goal is not None:
        out["goal"] = goal
    return out
