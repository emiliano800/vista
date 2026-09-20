"""Tier 3: run agent phases on synthetic companies and score against answer_key.json.

    uv run python scripts/eval_agents.py --company ridgeway --division 11_billing_ar
    uv run python scripts/eval_agents.py --sector industrial_goods --phase analyze
    VISTA_LLM_CASSETTE=tests/cassettes/ridgeway.json ... (replay hits, record misses when a key is set)

Phases: discover (facts -> data_quality predictions), execute (findings), analyze (opportunities).
Without an API key or cassette the stub answers, so scores are ~0; that is still a
useful smoke test of the pipeline. Prints JSON; exits 1 if trap_hits > 0."""

import argparse
import json
import sys
import uuid

from vista.agents import analyze, discover, execute, synthetic
from vista.agents.eval import Prediction, score
from vista.agents.llm import chat
from vista.agents.runtime import PhaseRun, run_phase


def _usage(runs: list[PhaseRun]) -> dict:
    return {
        "calls": len(runs),
        "input_tokens": sum(r.result.input_tokens for r in runs),
        "output_tokens": sum(r.result.output_tokens for r in runs),
        "cost_usd": str(sum(r.cost_usd for r in runs)),
        "sources": sorted({r.result.source for r in runs}),
    }


def eval_discover(company: synthetic.Company, division: str | None) -> tuple[list[Prediction], list[PhaseRun]]:
    runs, preds = [], []
    for table in synthetic.tables_for(company, division):
        profile = discover.profile_table(table)
        run = run_phase(discover.prepare(company, profile), discover.parse, lambda out, p=profile: discover.apply(out, p), llm=chat)
        runs.append(run)
        # Discover only reports facts; every non-trivial fact is scored as a data_quality prediction.
        for fact in run.rows:
            if fact["predicate"] not in ("format",):
                preds.append(
                    Prediction.from_finding(
                        company.short,
                        {"kind": "data_quality", "title": f"{fact['subject']} {fact['predicate']}", "source_ref": fact["source_ref"]},
                    )
                )
    return preds, runs


def eval_execute(company: synthetic.Company, division: str, scopes: set[str]) -> tuple[list[Prediction], list[PhaseRun]]:
    tables = synthetic.tables_for(company, division)
    refs = {t.ref for t in tables}
    run = run_phase(
        execute.prepare(company, division, tables, []), execute.parse, lambda out: execute.apply(out, scopes, set(), refs), llm=chat
    )
    preds = [Prediction.from_finding(company.short, row) for row in run.rows if row["tool"] == "create_finding"]
    return preds, [run]


def eval_analyze(sector: str, kinds: list[str]) -> tuple[list[Prediction], list[PhaseRun]]:
    """One model call per opportunity kind, each seeing only that kind's table types (csv or legacy xlsx sheet)."""
    by_company = {c: synthetic.tables_for(c) for c in synthetic.companies() if c.sector == sector}
    shorts = {c.short for c in by_company}
    preds, runs = [], []
    for kind in kinds:
        refs = {t.ref for tables in by_company.values() for t in analyze.tables_for_kind(kind, tables)}
        run = run_phase(
            analyze.prepare(sector, by_company, kind),
            lambda text, k=kind: analyze.parse(text, k),
            lambda out, r=refs: analyze.apply(out, shorts, r),
            llm=chat,
        )
        runs.append(run)
        preds.extend(Prediction.from_opportunity(row) for row in run.rows)
    return preds, runs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", choices=["discover", "execute", "analyze"], default="discover")
    ap.add_argument("--company", help="short name or slug (discover/execute)")
    ap.add_argument("--division", help="folder, e.g. 11_billing_ar (optional for discover)")
    ap.add_argument("--sector", choices=synthetic.SECTORS, help="analyze")
    ap.add_argument("--scopes", default="findings:write,tasks:write")
    ap.add_argument("--kinds", help="comma-separated opportunity kinds for analyze (default: all for the sector)")
    ap.add_argument("--tenant", help="tenant schema to record the result in (eval_runs); needs VISTA_DATABASE_URL")
    args = ap.parse_args()

    items = synthetic.answer_key()
    if args.phase == "analyze":
        if not args.sector:
            ap.error("--sector is required for analyze")
        wanted = args.kinds.split(",") if args.kinds else analyze.SECTOR_KINDS[args.sector]
        preds, runs = eval_analyze(args.sector, wanted)
        companies = {c.short for c in synthetic.companies() if c.sector == args.sector}
        kinds = set(wanted)
    else:
        if not args.company:
            ap.error("--company is required")
        company = synthetic.company(args.company)
        companies = {company.short}
        if args.phase == "discover":
            preds, runs = eval_discover(company, args.division)
            kinds = {"data_quality"}
        else:
            if not args.division:
                ap.error("--division is required for execute")
            preds, runs = eval_execute(company, args.division, set(args.scopes.split(",")))
            kinds = execute.FINDING_KINDS

    result = score(preds, items, companies=companies, kinds=kinds)
    report = {
        "phase": args.phase,
        "companies": sorted(companies),
        "predictions": len(preds),
        "score": result.as_dict(),
        "usage": _usage(runs),
    }
    if args.tenant:
        report["eval_run_id"] = str(record(args, report, runs))
    print(json.dumps(report, indent=1))
    return 1 if result.trap_hits else 0


def record(args, report: dict, runs: list[PhaseRun]) -> uuid.UUID:
    from vista.api.evals import EvalCreate, build_eval_run
    from vista.db import tenant_session

    row = build_eval_run(
        EvalCreate(
            phase=args.phase,
            sector=args.sector,
            company=None if args.phase == "analyze" else synthetic.company(args.company).short,
            division=args.division,
            model=next((r.result.model for r in runs), None),
            predictions=report["predictions"],
            calls=len(runs),
            cost_usd=report["usage"]["cost_usd"],
            score=report["score"],
        )
    )
    with tenant_session(args.tenant) as session:
        session.add(row)
        session.commit()
        return row.id


if __name__ == "__main__":
    sys.exit(main())
