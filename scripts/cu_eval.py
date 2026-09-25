"""Computer-use evaluation harness (docs/computer_use_system.md step 9).

    uv run python scripts/cu_eval.py run dev                 # score tests/fixtures/cu/dev at HEAD
    uv run python scripts/cu_eval.py run test                # frozen test set (refuses if cases changed)
    uv run python scripts/cu_eval.py freeze test             # freeze the test set at today's cases
    uv run python scripts/cu_eval.py combine eval/cu/*.json  # totals across sets, one revision only

Reports land in eval/cu/<set>-<revision>.{json,md}; the markdown is the committed report template's
filled-in form. Synthetic cases are scored as smoke and never enter the benchmark totals.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from taskmining import evaluate  # noqa: E402

SETS = ROOT / "tests" / "fixtures" / "cu"
OUT = ROOT / "eval" / "cu"


def cmd_run(args: argparse.Namespace) -> int:
    evalset = evaluate.load_set(SETS / args.set)
    sha, dirty = evaluate.git_revision(ROOT)
    report = evaluate.evaluate(evalset, sha, dirty)
    OUT.mkdir(parents=True, exist_ok=True)
    stem = OUT / f"{evalset.name}-{sha[:12]}"
    stem.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    stem.with_suffix(".md").write_text(evaluate.markdown(report))
    print(evaluate.markdown(report))
    print(f"wrote {stem}.json / .md")
    return 0


def cmd_freeze(args: argparse.Namespace) -> int:
    manifest = evaluate.freeze(SETS / args.set, date.today().isoformat())
    print(f"{args.set} frozen {manifest['frozen']} hash {manifest['hash'][:12]} ({len(manifest['cases'])} cases)")
    return 0


def cmd_combine(args: argparse.Namespace) -> int:
    reports = [json.loads(Path(p).read_text()) for p in args.reports]
    print(json.dumps(evaluate.combine(reports), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("set", choices=[d.name for d in SETS.iterdir() if d.is_dir()])
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("freeze")
    p.add_argument("set")
    p.set_defaults(fn=cmd_freeze)
    p = sub.add_parser("combine")
    p.add_argument("reports", nargs="+")
    p.set_defaults(fn=cmd_combine)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except evaluate.EvalError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
