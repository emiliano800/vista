"""CRM lookup benchmark for the Computer Use Agent over a synthetic front end.

`tasks` derives a deterministic task set from a company's `data.js`: each task is a
`WorkflowDefinition` (goal, criteria, a small recorded-style `PlanGraph`), the inputs to bind,
and the ground truth the final screen must show. `score` reads the run results a harness
session produced and reports success against that ground truth *independently* of Jev's own
verification, plus steps, pauses, wrong targets, wall time and Jev spend.

    uv run python scripts/computer_use_benchmark.py tasks meridian_risk_partners \
        --base-url http://127.0.0.1:8765 --n 6 > /tmp/bench/tasks.json
    uv run python scripts/computer_use_benchmark.py score /tmp/bench/tasks.json /tmp/bench/results

A result file `<task_id>.json` is what the runner saved per task:
    {"run": <GET /workflow-runs/{id}>, "agent_run": <GET /runs/{agent_run_id}>,
     "final_text": "<facts.text of the final observation>", "seconds": 42.1, "cost_usd": "0.0011",
     "decisions": [{"step_id":..., "decision":...}]}
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from taskmining.state import edge_id, state_key  # noqa: E402

FRONT_ENDS = Path(__file__).resolve().parents[1] / "synthetic_data" / "front_end_work"

CRM = {
    "meridian_risk_partners": {
        "module": "Clients (CRM)",
        "search": "Search Clients…",
        "table": "clients",
        "id_col": "client_id",
        "name_col": "client_name",
        "detail_cols": ["account_manager_name", "producer_name", "billing_city", "status"],
        "related": ("contacts", "client_id", "full_name"),
    },
}


def load_data(slug: str) -> dict:
    s = (FRONT_ENDS / slug / "data.js").read_text()
    return json.loads(s[s.index("{") : s.rindex("}") + 1])


def rows(data: dict, table: str) -> list[dict]:
    t = data["tables"][table]
    return [dict(zip(t["columns"], r, strict=True)) for r in t["rows"]]


# ---- the graph a recording of "look a client up" would compile to -------------------------------

ROLE = "browser"
PROV = [{"source": "recording", "id": "synthetic-crm-benchmark", "event_ids": []}]


def node(activity: str, signature: list[str], terminal: bool = False) -> dict:
    return {
        "key": state_key(ROLE, activity, signature),
        "app_role": ROLE,
        "activity": activity,
        "signature": sorted(signature),
        "terminal": terminal,
    }


def edge(frm: dict, to: dict, action: str, control: str | None, slot: str | None, effect: list[str]) -> dict:
    return {
        "id": edge_id(frm["key"], to["key"], action, control, slot),
        "frm": frm["key"],
        "to": to["key"],
        "action_class": action,
        "control": control,
        "slot": slot,
        "produces": [],
        "effect": effect,
        "stats": {"support": 1, "recorded": 1},
        "provenance": PROV,
        "policy": "auto",
    }


def lookup_graph(cfg: dict, open_record: bool) -> dict:
    n0 = node("Blank browser before opening the agency workspace", [])
    n1 = node("Agency workspace open on the overview", ["dialog:AgencyWorkspace"])
    n2 = node("Clients module open; client search empty", ["dialog:AgencyWorkspace", "dialog:Clients"])
    n3 = node(
        "Clients table filtered to the client being looked up",
        ["dialog:AgencyWorkspace", "dialog:Clients", "field:CLIENT_NAME"],
        terminal=not open_record,
    )
    nodes = [n0, n1, n2, n3]
    edges = [
        edge(n0, n1, "navigate", "Agency workspace", "crm_url", ["dialog:AgencyWorkspace"]),
        edge(n1, n2, "click", cfg["module"], None, ["dialog:Clients"]),
        edge(n2, n3, "type_value", cfg["search"], "CLIENT_NAME", ["field:CLIENT_NAME"]),
    ]
    if open_record:
        n4 = node(
            "Client record open with its contacts, policies and activity",
            ["dialog:AgencyWorkspace", "dialog:Clients", "field:CLIENT_NAME", "rec:Client"],
            terminal=True,
        )
        nodes.append(n4)
        edges.append(edge(n3, n4, "click", "the matching client row", None, ["rec:Client"]))
    return {"start": [n0["key"]], "nodes": nodes, "edges": edges, "trajectories": 1}


def make_tasks(slug: str, base_url: str, n: int, seed: int) -> list[dict]:
    cfg = CRM[slug]
    data = load_data(slug)
    clients = rows(data, cfg["table"])
    contacts = rows(data, cfg["related"][0])
    rng = random.Random(seed)
    picked = rng.sample(clients, n)
    url = f"{base_url.rstrip('/')}/{slug}/index.html"
    tasks = []
    for i, c in enumerate(picked):
        tier = "lookup" if i % 2 == 0 else "open_record"
        name, cid = c[cfg["name_col"]], c[cfg["id_col"]]
        primary = next((k for k in contacts if k[cfg["related"][1]] == cid and k.get("is_primary") == "Y"), None)
        if tier == "lookup":
            goal = (
                f"Open the declared crm_url, go to the {cfg['module']} module and filter the Clients table to the client named in "
                f"CLIENT_NAME so that its row (account manager, producer, billing city, status) is visible. Do not open other modules."
            )
            criteria = [
                f"The Clients table shows the client {name} ({cid}).",
                f"The row for {name} shows account manager {c['account_manager_name']} and producer {c['producer_name']}.",
            ]
            expect = [name, cid, c["account_manager_name"], c["producer_name"]]
        else:
            goal = (
                f"Open the declared crm_url, go to the {cfg['module']} module, filter the Clients table to the client named in "
                f"CLIENT_NAME and open that client's record so its contacts and policies are visible."
            )
            criteria = [f"The record for {name} ({cid}) is open."]
            if primary:
                criteria.append(f"The client's contacts include {primary[cfg['related'][2]]}.")
            expect = [name, cid] + ([primary[cfg["related"][2]]] if primary else [])
        tasks.append(
            {
                "task_id": f"{slug}-{i + 1:02d}-{tier}",
                "tier": tier,
                "client": {"id": cid, "name": name},
                "definition": {
                    "goal": goal,
                    "required_inputs": ["crm_url", "CLIENT_NAME"],
                    "allowed_tools": ["lookup_reference", "draft_reply"],
                    "success_criteria": criteria,
                    "environment": "sandbox",
                    "limits": {"max_steps": 12, "max_runtime_seconds": 600, "max_cost_usd": "0.50"},
                    "graph": lookup_graph(cfg, open_record=tier == "open_record"),
                },
                "inputs": {"crm_url": {"kind": "value", "value": url}, "CLIENT_NAME": {"kind": "value", "value": name}},
                "expected": {"must_contain": expect, "hash": "#clients"},
            }
        )
    return tasks


# ---- scoring -----------------------------------------------------------------------------------


def score(tasks: list[dict], results_dir: Path) -> dict:
    per_task = []
    for t in tasks:
        f = results_dir / f"{t['task_id']}.json"
        if not f.exists():
            per_task.append({"task_id": t["task_id"], "tier": t["tier"], "status": "not_run"})
            continue
        r = json.loads(f.read_text())
        run = r.get("run") or {}
        text = str(r.get("final_text") or "")
        missing = [s for s in t["expected"]["must_contain"] if s not in text]
        events = (r.get("agent_run") or {}).get("events") or []
        data = [e.get("data") or {} for e in events]
        acts = [
            d
            for e, d in zip(events, data, strict=True)
            if e.get("event_type") == "tool_call" and d.get("executed") and d.get("primitive") != "observe"
        ]
        plans = [
            d
            for e, d in zip(events, data, strict=True)
            if e.get("event_type") == "model_call" and d.get("phase") == "plan" and d.get("target")
        ]
        controls = {e["id"]: e.get("control") or "" for e in t["definition"]["graph"]["edges"]}
        wrong_target = 0
        for d in plans:
            control, label = controls.get(d.get("edge"), "").lower(), str(d["target"]).lower()
            fits = control in label or ("row" in control and (t["client"]["name"].lower() in label or t["client"]["id"].lower() in label))
            wrong_target += 0 if fits else 1
        verification = ((run.get("result") or {}).get("verification")) or r.get("verification") or {}
        for d in reversed(data):
            if d.get("phase") == "verify":
                verification = verification or d
                break
        per_task.append(
            {
                "task_id": t["task_id"],
                "tier": t["tier"],
                "status": run.get("status"),
                "ground_truth_ok": not missing and run.get("status") == "succeeded",
                "missing_on_screen": missing,
                "jev_verified": verification.get("passed"),
                "p_goal": verification.get("p_goal"),
                "steps": len(acts) or run.get("steps"),
                "pauses": len(r.get("decisions") or []),
                "wrong_target": wrong_target,
                "seconds": r.get("seconds"),
                "cost_usd": r.get("cost_usd"),
            }
        )
    ran = [p for p in per_task if p.get("status") not in (None, "not_run")]
    ok = [p for p in ran if p["ground_truth_ok"]]
    agree = [p for p in ran if p.get("jev_verified") is not None and p["jev_verified"] == p["ground_truth_ok"]]
    summary = {
        "tasks": len(tasks),
        "ran": len(ran),
        "ground_truth_success": f"{len(ok)}/{len(ran)}",
        "jev_verifier_agreement": f"{len(agree)}/{len(ran)}",
        "wrong_targets": sum(p["wrong_target"] for p in ran),
        "pauses": sum(p["pauses"] for p in ran),
        "mean_steps": round(sum(p["steps"] or 0 for p in ran) / len(ran), 2) if ran else None,
        "mean_seconds": round(sum(float(p["seconds"] or 0) for p in ran) / len(ran), 1) if ran else None,
        "total_cost_usd": round(sum(float(p["cost_usd"] or 0) for p in ran), 6),
        "by_tier": {
            tier: f"{sum(1 for p in ran if p['tier'] == tier and p['ground_truth_ok'])}/{sum(1 for p in ran if p['tier'] == tier)}"
            for tier in ("lookup", "open_record")
        },
    }
    return {"summary": summary, "tasks": per_task}


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tasks")
    t.add_argument("slug", choices=sorted(CRM))
    t.add_argument("--base-url", default="http://127.0.0.1:8765")
    t.add_argument("--n", type=int, default=6)
    t.add_argument("--seed", type=int, default=7)
    s = sub.add_parser("score")
    s.add_argument("tasks")
    s.add_argument("results_dir")
    a = ap.parse_args()
    if a.cmd == "tasks":
        json.dump(make_tasks(a.slug, a.base_url, a.n, a.seed), sys.stdout, indent=2, ensure_ascii=False)
    else:
        json.dump(score(json.loads(Path(a.tasks).read_text()), Path(a.results_dir)), sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
