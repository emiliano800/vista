"""Entry point: regenerate every synthetic dataset.

    python3 synthetic_data/generator/generate.py

Output layout (relative to synthetic_data/):

    insurance_broking/<company>/<NN_workflow>/<dataset>.(csv|xlsx|json|eml|md)
    industrial_goods/<company>/<NN_workflow>/...
    answer_key.json / ANSWER_KEY.md   -- every planted anomaly and synergy
    manifest.json                     -- every file written, with row counts
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import AnswerKey  # noqa: E402
from industrial import generate_industrial  # noqa: E402
from insurance import generate_insurance  # noqa: E402
from portfolio import register_portfolio_findings  # noqa: E402
from profiles import INDUSTRIAL_COMPANIES, INSURANCE_COMPANIES  # noqa: E402

OUT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def main() -> None:
    key = AnswerKey()
    manifest: dict = {"as_of_date": "2026-03-31", "sectors": {}}

    ins_root = os.path.join(OUT, "insurance_broking")
    os.makedirs(ins_root, exist_ok=True)
    manifest["sectors"]["insurance_broking"] = [generate_insurance(p, ins_root, key) for p in INSURANCE_COMPANIES]

    ind_root = os.path.join(OUT, "industrial_goods")
    os.makedirs(ind_root, exist_ok=True)
    manifest["sectors"]["industrial_goods"] = [generate_industrial(p, ind_root, key) for p in INDUSTRIAL_COMPANIES]

    register_portfolio_findings(key, manifest["sectors"])

    with open(os.path.join(OUT, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    with open(os.path.join(OUT, "answer_key.json"), "w") as f:
        json.dump(key.items, f, indent=2)
    with open(os.path.join(OUT, "ANSWER_KEY.md"), "w") as f:
        f.write(render_answer_key(key))

    for sector, comps in manifest["sectors"].items():
        for c in comps:
            n_files = len(c["files"])
            n_rows = sum(x["rows"] or 0 for x in c["files"])
            print(f"{sector:18s} {c['slug']:36s} tier={c['tier']:6s} files={n_files:3d} rows={n_rows:6d} dropped={len(c['dropped_datasets'])} merged={len(c['merged_into_legacy_workbook'])}")
    print(f"answer key items: {len(key.items)}")


def render_answer_key(key: AnswerKey) -> str:
    lines = ["# Answer Key — planted anomalies and portfolio findings", "",
             "Use this to check whether Vista's agents find what was planted. Items marked **TRAP** are",
             "deliberately misleading and the correct behaviour is to *not* merge / *not* flag them.", ""]
    by_kind: dict[str, list[dict]] = {}
    for it in key.items:
        by_kind.setdefault(it["kind"], []).append(it)
    for kind, items in sorted(by_kind.items()):
        lines.append(f"## {kind}")
        lines.append("")
        for it in items:
            trap = " **TRAP**" if it["is_false_positive_trap"] else ""
            lines.append(f"### {it['id']} — {it['title']}{trap}")
            lines.append(f"- Companies: {', '.join(it['companies'])}")
            lines.append(f"- {it['description']}")
            lines.append(f"- Evidence: {', '.join('`' + e + '`' for e in it['evidence'])}")
            lines.append(f"- Expected action: {it['expected_action']}")
            lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
