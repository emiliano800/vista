"""Evaluation harness for the v3 task graph (step 9 of docs/computer_use_system.md).

A *set* is a directory of cases (`tests/fixtures/cu/<set>/`) with a `set.json` manifest. A case
is one recording-compiled graph plus the evidence it is judged on:

    {"id": "...", "kind": "recording" | "synthetic", "apps": [...], "people": [...],
     "tasks": [...], "graph": "<file>" | {...},
     "held_out": [{"l0": [...], "expected": "<node key>" | null, "recording": "rec-2"}],
     "runs": [{"run_id": "...", "verified": true | false | null, "rejudged": 0,
               "recovery_used": 0, "leakage_failed": 0, "edges": ["<edge id>", ...]}]}

Held-out frames come from recordings *not* used to compile the graph; an `expected` of `null`
means the frame is a state the graph should not claim (`in:<unknown>`). Runs contribute
statistics only. The harness computes, per case and per set:

    locate_accuracy   held-out frames located on exactly the expected node (or correctly on none)
    coverage          held-out frames the graph has a node for  /  run moves the graph has an edge for
    rejudged          Jev re-asked over the located state's own moves
    recovery_used     recovery fragment entries
    leakage_failures  graph leakage check failures + cloud-bound observations that failed in runs
    verified_rate     runs that passed verification / runs with a verdict

Rules the harness enforces rather than documents:

- only recording-compiled graphs (`compiled_by` = `recorder-plan/<n>`, provenance `recording`
  and `run` only) are evaluated; anything else is rejected, not scored;
- `kind: synthetic` cases are smoke only — they are scored, but excluded from `benchmark`
  totals, so the synthetic CRM can never appear in a performance number;
- a report is tied to one git revision; `combine()` refuses reports from different revisions;
- the `test` set is frozen: `set.json` carries `frozen: <date>` and a manifest hash, and
  `load_set` refuses a test set whose cases no longer match the hash.

Every threshold constant in this package is provisional until milestone 1 replaces it with a
number from this harness.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from taskmining import leakage
from taskmining.normalise import Vocabulary

COMPILED_BY_PREFIX = "recorder-plan/"
PROVENANCE_SOURCES: frozenset[str] = frozenset({"recording", "run"})
CASE_KINDS: frozenset[str] = frozenset({"recording", "synthetic"})

MILESTONE_1 = {"recordings": 3, "tasks": 5, "apps": 2, "people": 2}
MILESTONE_2 = {"write_tasks_per_app": 1}


class EvalError(ValueError):
    pass


# ---- graph admission --------------------------------------------------------------------------------


def admit_graph(graph: dict) -> None:
    """Recording-compiled graphs only. Raises EvalError otherwise."""
    compiled_by = str(graph.get("compiled_by") or "")
    if not compiled_by.startswith(COMPILED_BY_PREFIX):
        raise EvalError(f"graph compiled_by={compiled_by!r}: only {COMPILED_BY_PREFIX}<n> graphs are evaluated")
    if not graph.get("nodes") or any(n.get("l0") is None for n in graph["nodes"]):
        raise EvalError("graph has no v3 nodes (every node needs `l0`)")
    for e in graph.get("edges") or []:
        prov = e.get("provenance") or []
        if not prov:
            raise EvalError(f"edge {e.get('id')} has no provenance")
        for p in prov:
            if p.get("source") not in PROVENANCE_SOURCES:
                raise EvalError(f"edge {e.get('id')} provenance source {p.get('source')!r} is not a recording or a run")


def graph_recordings(graph: dict) -> set[str]:
    return {p["id"] for e in graph.get("edges") or [] for p in e.get("provenance") or [] if p.get("source") == "recording"}


# ---- locate (same rule as computer_use/run_v3.locate: L0 set equality) --------------------------------


def locate(graph: dict, l0: Iterable[str]) -> list[str]:
    want = frozenset(str(t) for t in l0)
    return sorted(n["key"] for n in graph["nodes"] if frozenset(n["l0"]) == want)


# ---- metrics ----------------------------------------------------------------------------------------


@dataclass
class Metrics:
    held_out: int = 0
    located_correct: int = 0
    located_any: int = 0
    ambiguous: int = 0
    run_moves: int = 0
    run_moves_on_graph: int = 0
    runs: int = 0
    runs_with_verdict: int = 0
    verified_ok: int = 0
    rejudged: int = 0
    recovery_used: int = 0
    leakage_failures: int = 0

    def add(self, other: Metrics) -> None:
        for k, v in vars(other).items():
            setattr(self, k, getattr(self, k) + v)

    def ratios(self) -> dict[str, float | None]:
        def r(a: int, b: int) -> float | None:
            return None if b == 0 else round(a / b, 4)

        return {
            "locate_accuracy": r(self.located_correct, self.held_out),
            "frame_coverage": r(self.located_any, self.held_out),
            "move_coverage": r(self.run_moves_on_graph, self.run_moves),
            "verified_rate": r(self.verified_ok, self.runs_with_verdict),
        }

    def to_json(self) -> dict:
        return {**vars(self), **self.ratios()}


def score_case(case: dict, graph: dict) -> Metrics:
    admit_graph(graph)
    m = Metrics()
    compiled_from = graph_recordings(graph)
    for frame in case.get("held_out") or []:
        rec = frame.get("recording")
        if rec is not None and rec in compiled_from:
            raise EvalError(f"case {case['id']}: held-out frame from {rec} which the graph was compiled from")
        found = locate(graph, frame["l0"])
        expected = frame.get("expected")
        m.held_out += 1
        if found:
            m.located_any += 1
        if len(found) > 1:
            m.ambiguous += 1
        if (expected is None and not found) or (expected is not None and found == [expected]):
            m.located_correct += 1
    edge_ids = {e["id"] for e in graph["edges"]}
    for run in case.get("runs") or []:
        m.runs += 1
        verified = run.get("verified")
        if verified is not None:
            m.runs_with_verdict += 1
            m.verified_ok += 1 if verified else 0
        m.rejudged += int(run.get("rejudged") or 0)
        m.recovery_used += int(run.get("recovery_used") or 0)
        m.leakage_failures += int(run.get("leakage_failed") or 0)
        for eid in run.get("edges") or []:
            m.run_moves += 1
            m.run_moves_on_graph += 1 if eid in edge_ids else 0
    vocab_json = graph.get("vocabulary")
    vocab = Vocabulary.from_json(vocab_json) if vocab_json else Vocabulary()
    report = leakage.check({k: v for k, v in graph.items() if k != "vocabulary"}, leakage.RecordingContext.build(vocab=vocab))
    m.leakage_failures += len(report.failures)
    return m


# ---- sets ------------------------------------------------------------------------------------------


@dataclass
class EvalSet:
    name: str
    root: Path
    frozen: str | None
    cases: list[dict] = field(default_factory=list)
    graphs: dict[str, dict] = field(default_factory=dict)


def manifest_hash(root: Path, case_files: list[str]) -> str:
    h = hashlib.sha256()
    for name in case_files:
        h.update(name.encode())
        h.update((root / name).read_bytes())
        case = json.loads((root / name).read_text())
        if isinstance(case.get("graph"), str):
            h.update((root / case["graph"]).read_bytes())
    return h.hexdigest()


def load_set(root: Path) -> EvalSet:
    manifest = json.loads((root / "set.json").read_text())
    files = list(manifest.get("cases") or [])
    frozen = manifest.get("frozen")
    if frozen:
        want = manifest.get("hash")
        have = manifest_hash(root, files)
        if want != have:
            raise EvalError(
                f"set {root.name} is frozen since {frozen} but its cases changed "
                f"(hash {have[:12]} != {str(want)[:12]}); a changed set is a new set"
            )
    out = EvalSet(name=manifest.get("name") or root.name, root=root, frozen=frozen)
    seen: set[str] = set()
    for name in files:
        case = json.loads((root / name).read_text())
        if case["id"] in seen:
            raise EvalError(f"duplicate case id {case['id']}")
        seen.add(case["id"])
        if case.get("kind") not in CASE_KINDS:
            raise EvalError(f"case {case['id']}: kind must be one of {sorted(CASE_KINDS)}")
        graph = case["graph"]
        if isinstance(graph, str):
            graph = json.loads((root / graph).read_text())
        admit_graph(graph)
        out.cases.append(case)
        out.graphs[case["id"]] = graph
    return out


def freeze(root: Path, today: str) -> dict:
    manifest = json.loads((root / "set.json").read_text())
    manifest["frozen"] = today
    manifest["hash"] = manifest_hash(root, list(manifest.get("cases") or []))
    (root / "set.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


# ---- reports (one revision each) -------------------------------------------------------------------------


def git_revision(repo: Path) -> tuple[str, bool]:
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return "unknown", True
    return sha, dirty


def evaluate(evalset: EvalSet, revision: str, dirty: bool) -> dict:
    cases_out = []
    benchmark = Metrics()
    smoke = Metrics()
    for case in evalset.cases:
        m = score_case(case, evalset.graphs[case["id"]])
        (smoke if case["kind"] == "synthetic" else benchmark).add(m)
        cases_out.append({"id": case["id"], "kind": case["kind"], "apps": sorted(case.get("apps") or []), "metrics": m.to_json()})
    return {
        "harness": "cu-eval/1",
        "revision": revision,
        "dirty": dirty,
        "set": evalset.name,
        "frozen": evalset.frozen,
        "cases": cases_out,
        "benchmark": benchmark.to_json(),
        "smoke": smoke.to_json(),
        "milestones": milestones(evalset),
    }


def combine(reports: list[dict]) -> dict:
    """Totals over several sets of the *same* revision. Never across revisions."""
    revisions = {r["revision"] for r in reports}
    if len(revisions) != 1:
        raise EvalError(f"refusing to combine reports from different revisions: {sorted(revisions)}")
    if any(r.get("dirty") for r in reports):
        raise EvalError("refusing to combine reports from a dirty working tree")
    bench = Metrics()
    smoke = Metrics()
    for r in reports:
        bench.add(Metrics(**{k: r["benchmark"][k] for k in vars(Metrics())}))
        smoke.add(Metrics(**{k: r["smoke"][k] for k in vars(Metrics())}))
    return {
        "harness": "cu-eval/1",
        "revision": revisions.pop(),
        "sets": [r["set"] for r in reports],
        "benchmark": bench.to_json(),
        "smoke": smoke.to_json(),
    }


# ---- milestones ------------------------------------------------------------------------------------------


def milestones(evalset: EvalSet) -> dict:
    real = [c for c in evalset.cases if c["kind"] == "recording"]
    recordings: set[str] = set()
    tasks: set[str] = set()
    apps: set[str] = set()
    people: set[str] = set()
    write_tasks_by_app: dict[str, set[str]] = {}
    for c in real:
        g = evalset.graphs[c["id"]]
        recordings |= graph_recordings(g)
        recordings |= {f["recording"] for f in c.get("held_out") or [] if f.get("recording")}
        tasks |= set(c.get("tasks") or [c["id"]])
        apps |= set(c.get("apps") or [])
        people |= set(c.get("people") or [])
        if any(e.get("irreversibility") in ("mutating", "committing") for e in g["edges"]):
            for a in c.get("apps") or []:
                write_tasks_by_app.setdefault(a, set()).update(c.get("tasks") or [c["id"]])
    have = {"recordings": len(recordings), "tasks": len(tasks), "apps": len(apps), "people": len(people)}
    m1_unmet = {k: v for k, v in MILESTONE_1.items() if have[k] < v}
    m2_unmet = [a for a in sorted(apps) if len(write_tasks_by_app.get(a, ())) < MILESTONE_2["write_tasks_per_app"]]
    return {
        "have": have,
        "milestone_1": {"met": not m1_unmet, "unmet": m1_unmet},
        "milestone_2": {"met": bool(apps) and not m2_unmet, "apps_without_write_task": m2_unmet},
    }


# ---- markdown -----------------------------------------------------------------------------------------------


def markdown(report: dict) -> str:
    def pct(v: float | None) -> str:
        return "not measured" if v is None else f"{v * 100:.1f}%"

    def ratio(m: dict, key: str, num: str, den: str) -> str:
        return f"{pct(m[key])} ({m[num]}/{m[den]})"

    b = report["benchmark"]
    s = report["smoke"]
    ms = report.get("milestones") or {}
    dirty = " (dirty)" if report.get("dirty") else ""
    lines = [
        f"# Computer-use evaluation — set `{report['set']}` @ `{report['revision'][:12]}`{dirty}",
        "",
        f"Frozen: {report.get('frozen') or 'no (dev set)'}. Numbers are for this revision only "
        "and are never combined with another revision's.",
        "",
        "| metric | benchmark (recordings) | smoke (synthetic, not a claim) |",
        "| --- | --- | --- |",
        f"| locate accuracy | {ratio(b, 'locate_accuracy', 'located_correct', 'held_out')} "
        f"| {ratio(s, 'locate_accuracy', 'located_correct', 'held_out')} |",
        f"| frame coverage | {pct(b['frame_coverage'])} | {pct(s['frame_coverage'])} |",
        f"| move coverage | {ratio(b, 'move_coverage', 'run_moves_on_graph', 'run_moves')} | {pct(s['move_coverage'])} |",
        f"| verified rate | {ratio(b, 'verified_rate', 'verified_ok', 'runs_with_verdict')} | {pct(s['verified_rate'])} |",
        f"| rejudged | {b['rejudged']} | {s['rejudged']} |",
        f"| recovery used | {b['recovery_used']} | {s['recovery_used']} |",
        f"| leakage failures | {b['leakage_failures']} | {s['leakage_failures']} |",
        f"| ambiguous locates | {b['ambiguous']} | {s['ambiguous']} |",
        "",
    ]
    if ms:
        have = ms["have"]
        lines += [
            f"Milestone 1 ({', '.join(f'≥{v} {k}' for k, v in MILESTONE_1.items())}): "
            + ("**met**" if ms["milestone_1"]["met"] else "not met — have " + ", ".join(f"{have[k]} {k}" for k in MILESTONE_1)),
            "Milestone 2 (≥1 write task per app): "
            + (
                "**met**"
                if ms["milestone_2"]["met"]
                else "not met — no write task for: " + (", ".join(ms["milestone_2"]["apps_without_write_task"]) or "(no real apps yet)")
            ),
            "",
        ]
    lines += ["| case | kind | apps | locate | frames | moves | verified |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for c in report["cases"]:
        m = c["metrics"]
        lines.append(
            f"| {c['id']} | {c['kind']} | {', '.join(c['apps']) or '—'} | {pct(m['locate_accuracy'])} "
            f"| {m['held_out']} | {m['run_moves']} | {pct(m['verified_rate'])} |"
        )
    return "\n".join(lines) + "\n"
