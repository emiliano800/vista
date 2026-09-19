"""CLI: ``python -m taskmining run --synthetic --out out/``"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from taskmining.annotations import read_annotations, write_annotations
from taskmining.capture import JsonlSource, SyntheticSource
from taskmining.models import write_jsonl
from taskmining.pipeline import Pipeline


def _print_report(res) -> None:
    d = res.discovery
    print(
        f"raw events: {len(res.raw)}  clean events: {len(res.clean)}  steps: {len(res.steps)}  cases: {len(d.cases)}"
        f"  annotations: {len(res.annotations)}  open questions: {len(res.questions)}"
    )
    src = Counter(s.activity_source.value for s in res.steps)
    csrc = Counter(s.case_source.value if s.case_source else "none" for s in res.steps)
    print(f"activity provenance: {dict(src)}")
    print(f"case provenance:     {dict(csrc)}")
    print("\nTop variants:")
    for v in d.variants[:5]:
        print(f"  {v.count:3d}x  {v.mean_throughput_s:6.1f}s  " + " -> ".join(v.activities))
    print("\nActivities by total time:")
    for a in d.activities:
        print(f"  {a.total_s:8.1f}s  {a.count:4d}x  mean {a.mean_s:6.2f}s  {a.activity}")
    if d.rework:
        print("\nRework (repeated activities within a case):")
        for act, n in d.rework.most_common():
            print(f"  {n:3d}  {act}")
    print("\nAutomation potential:")
    print("  score  freq  regul  xfer   switch hours  activity")
    for a in res.automation:
        print(
            f"  {a.score:.2f}   {a.frequency:.2f}  {a.regularity:.2f}   {a.data_transfer:.2f}"
            f"   {a.app_switch:.2f}   {a.hours_total:.2f}   {a.activity}"
        )
    if res.questions:
        print("\nOpen questions for the guided interview:")
        for q in res.questions[:8]:
            print(f"  [{q.kind}] {q.user}: {q.prompt}")
        if len(res.questions) > 8:
            print(f"  ... {len(res.questions) - 8} more in questions.json")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="taskmining")
    sub = p.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run the full pipeline")
    src = run.add_mutually_exclusive_group(required=True)
    src.add_argument("--synthetic", type=int, metavar="N_CASES", help="generate N synthetic cases")
    src.add_argument("--input", type=Path, help="JSONL file of raw events")
    run.add_argument("--out", type=Path, default=Path("out"))
    run.add_argument("--seed", type=int, default=7)
    run.add_argument("--no-pseudonymize", action="store_true")
    run.add_argument("--no-episodes", action="store_true", help="disable synthetic episode cases for id-less steps")
    run.add_argument(
        "--annotations",
        type=Path,
        help="human annotations (JSONL or CSV: user,start,end,label[,note,case_id,author]); "
        "with --synthetic, 'auto' uses the generator's annotations",
    )

    gen = sub.add_parser("generate", help="write synthetic raw events to JSONL")
    gen.add_argument("--cases", type=int, default=40)
    gen.add_argument("--seed", type=int, default=7)
    gen.add_argument("--out", type=Path, required=True)
    gen.add_argument("--annotations-out", type=Path, help="also write the generator's employee/analyst annotations")

    args = p.parse_args(argv)
    if args.cmd == "generate":
        src = SyntheticSource(args.cases, args.seed)
        events = list(src.events())
        with args.out.open("w") as fp:
            write_jsonl(events, fp)
        print(f"wrote {len(events)} events to {args.out}")
        if args.annotations_out:
            with args.annotations_out.open("w") as fp:
                write_annotations(src.annotations(), fp)
            print(f"wrote {len(src.annotations())} annotations to {args.annotations_out}")
        return 0

    pipeline = Pipeline(pseudonymize=not args.no_pseudonymize, episode_fallback=not args.no_episodes)
    human = None
    if args.annotations and str(args.annotations) != "auto":
        with args.annotations.open() as fp:
            human = read_annotations(fp, fmt="csv" if args.annotations.suffix.lower() == ".csv" else "jsonl")
    if args.synthetic is not None:
        src = SyntheticSource(args.synthetic, args.seed)
        if args.annotations and str(args.annotations) == "auto":
            human = src.annotations()
        res = pipeline.run(src, human)
    else:
        with args.input.open() as fp:
            res = pipeline.run(JsonlSource(fp), human)
    res.write(args.out)
    _print_report(res)
    print(f"\nartifacts written to {args.out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
