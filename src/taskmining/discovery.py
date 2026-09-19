"""Process discovery over the abstracted event log."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from statistics import mean, median

from taskmining.models import Step

START = "▶ start"
END = "■ end"


@dataclass
class Edge:
    src: str
    dst: str
    count: int
    mean_wait_s: float


@dataclass
class Variant:
    activities: tuple[str, ...]
    cases: list[str]
    mean_throughput_s: float

    @property
    def count(self) -> int:
        return len(self.cases)


@dataclass
class ActivityStats:
    activity: str
    count: int
    total_s: float
    mean_s: float
    median_s: float
    n_cases: int


@dataclass
class DiscoveryResult:
    cases: dict[str, list[Step]]
    edges: list[Edge]
    variants: list[Variant]
    activities: list[ActivityStats]
    rework: Counter = field(default_factory=Counter)

    def to_dot(self, min_count: int = 1) -> str:
        lines = ["digraph task_mining {", "  rankdir=LR;", "  node [shape=box, style=rounded];"]
        for e in self.edges:
            if e.count < min_count:
                continue
            lines.append(f'  "{e.src}" -> "{e.dst}" [label="{e.count}\\n{e.mean_wait_s:.1f}s"];')
        lines.append("}")
        return "\n".join(lines)

    def to_mermaid(self, min_count: int = 1) -> str:
        ids: dict[str, str] = {}

        def nid(name: str) -> str:
            if name not in ids:
                ids[name] = f"n{len(ids)}"
            return ids[name]

        lines = ["flowchart LR"]
        for e in self.edges:
            if e.count < min_count:
                continue
            lines.append(f'  {nid(e.src)}["{e.src}"] -->|{e.count}| {nid(e.dst)}["{e.dst}"]')
        return "\n".join(lines)


def group_cases(steps: list[Step]) -> dict[str, list[Step]]:
    cases: dict[str, list[Step]] = defaultdict(list)
    for s in steps:
        if s.case_id:
            cases[s.case_id].append(s)
    for seq in cases.values():
        seq.sort(key=lambda s: s.start)
    return dict(cases)


def discover(steps: list[Step]) -> DiscoveryResult:
    cases = group_cases(steps)

    edge_count: Counter[tuple[str, str]] = Counter()
    edge_wait: dict[tuple[str, str], list[float]] = defaultdict(list)
    variants: dict[tuple[str, ...], list[str]] = defaultdict(list)
    throughput: dict[tuple[str, ...], list[float]] = defaultdict(list)
    rework: Counter[str] = Counter()
    act_dur: dict[str, list[float]] = defaultdict(list)
    act_cases: dict[str, set[str]] = defaultdict(set)

    for cid, seq in cases.items():
        names = tuple(s.activity for s in seq)
        prev = START
        prev_end = seq[0].start
        seen: Counter[str] = Counter()
        for s in seq:
            edge_count[(prev, s.activity)] += 1
            edge_wait[(prev, s.activity)].append((s.start - prev_end).total_seconds())
            prev, prev_end = s.activity, s.end
            seen[s.activity] += 1
            act_dur[s.activity].append(s.duration_s)
            act_cases[s.activity].add(cid)
        edge_count[(prev, END)] += 1
        edge_wait[(prev, END)].append(0.0)
        for a, n in seen.items():
            if n > 1:
                rework[a] += n - 1
        variants[names].append(cid)
        throughput[names].append((seq[-1].end - seq[0].start).total_seconds())

    edges = sorted(
        (Edge(a, b, c, mean(edge_wait[(a, b)])) for (a, b), c in edge_count.items()),
        key=lambda e: -e.count,
    )
    vs = sorted(
        (Variant(k, v, mean(throughput[k])) for k, v in variants.items()),
        key=lambda v: -v.count,
    )
    acts = sorted(
        (ActivityStats(a, len(d), sum(d), mean(d), median(d), len(act_cases[a])) for a, d in act_dur.items()),
        key=lambda a: -a.total_s,
    )
    return DiscoveryResult(cases, edges, vs, acts, rework)
