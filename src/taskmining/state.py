"""Workflow state abstraction and plan-graph algebra, shared by every producer and consumer
of a `PlanGraph`.

A recorded workflow is not a script but a graph over *states*: the app's role, an activity
label, and a `data_signature` — which typed things are held right now (a document open, a
field filled, a fact gathered), by *name* only. Recordings and runs are trajectories through
that graph; each traversal adds counts and provenance to an edge, never a value. The recorder
(`src/recorder/src/plan.js`) compiles trajectories with the exact same key functions below, so
a node the device produced and a node the worker observes at run time compare by key alone.
Everything here is deterministic; no model is involved.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

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

SIGNATURE_TOKEN = re.compile(r"^(?:(?:doc|rec|field|fact|dialog):[A-Za-z0-9_.-]{1,120}|msg:open)$")
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
    return _h((role, activity, ",".join(sorted(set(signature)))))


def edge_id(frm: str, to: str, action_class: str, control: str | None, slot: str | None) -> str:
    return _h((frm, to, action_class, control or "", slot or ""))


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def near(node: dict, other: dict) -> bool:
    """Same role and most of the signature in common: the candidates `on_plan` judges among."""
    return node["app_role"] == other["app_role"] and jaccard(node.get("signature", ()), other.get("signature", ())) >= NEAR_JACCARD


def default_policy(action_class: str) -> str:
    if action_class == "submit":
        return "always_ask"
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
    trajectories = 0
    truncated = False
    for g in graphs:
        trajectories += int(g.get("trajectories", 0))
        truncated = truncated or bool(g.get("truncated"))
        start.update(g.get("start", []))
        for n in g.get("nodes", []):
            cur = nodes.get(n["key"])
            if cur is None:
                nodes[n["key"]] = {**n, "signature": sorted(n.get("signature", [])), "terminal": bool(n.get("terminal"))}
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
                    "policy": e.get("policy") or default_policy(e["action_class"]),
                }
                continue
            for k in cur["stats"]:
                cur["stats"][k] += int(e.get("stats", {}).get(k, 0))
            cur["provenance"] = sorted(cur["provenance"] + list(e.get("provenance", [])), key=_prov_key)[:MAX_PROVENANCE]
            ref = e.get("anchor_ref")
            if ref and (not cur.get("anchor_ref") or ref < cur["anchor_ref"]):
                cur["anchor_ref"] = ref
            cur["policy"] = stricter(cur["policy"], e.get("policy") or default_policy(e["action_class"]))
            cur["produces"] = sorted(set(cur["produces"]) | set(e.get("produces", [])))
            cur["effect"] = sorted(set(cur["effect"]) | set(e.get("effect", [])))
    return {
        "start": sorted(start),
        "nodes": [nodes[k] for k in sorted(nodes)],
        "edges": [edges[k] for k in sorted(edges)],
        "trajectories": trajectories,
        "truncated": truncated,
        "compiled_by": next((g.get("compiled_by") for g in graphs if g.get("compiled_by")), "recorder-plan/1"),
    }
