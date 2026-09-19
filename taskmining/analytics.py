"""Automation potential scoring.

Celonis surfaces "automation opportunities" by ranking activities on how
repetitive, rule-based and data-transfer heavy they are. The heuristic here
combines:

* frequency      - how often the activity occurs (share of all steps)
* regularity     - low duration variance implies a routine, scriptable task
* data transfer  - copy/paste and typing volume signal manual re-keying; a paste
                   whose content was copied in *another* app (a linked transfer)
                   counts double because it is proven re-keying, not a guess
* app switching  - activities that sit between different apps are swivel-chair

``data_flows`` aggregates those linked transfers into "from app -> to app"
edges: the swivel-chair map an integration would remove.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from statistics import mean, pstdev

from taskmining.abstraction import is_transfer
from taskmining.models import RawEvent, Step


@dataclass
class AutomationScore:
    activity: str
    score: float
    frequency: float
    regularity: float
    data_transfer: float
    app_switch: float
    hours_total: float

    def as_row(self) -> list[str]:
        return [
            self.activity,
            f"{self.score:.2f}",
            f"{self.frequency:.2f}",
            f"{self.regularity:.2f}",
            f"{self.data_transfer:.2f}",
            f"{self.app_switch:.2f}",
            f"{self.hours_total:.2f}",
        ]


@dataclass
class DataFlow:
    source_app: str
    target_app: str
    count: int
    mean_transfer_s: float
    chars: int

    def to_dict(self) -> dict:
        return {
            "from": self.source_app,
            "to": self.target_app,
            "count": self.count,
            "mean_transfer_s": round(self.mean_transfer_s, 2),
            "chars": self.chars,
        }


def data_flows(events: list[RawEvent]) -> list[DataFlow]:
    """Linked copy->paste transfers between apps, most frequent first."""
    acc: dict[tuple[str, str], list[RawEvent]] = defaultdict(list)
    for e in events:
        if is_transfer(e):
            acc[(str(e.payload["source_app"]), e.app)].append(e)
    out = []
    for (src, dst), evs in acc.items():
        secs = [float(e.payload.get("transfer_ms", 0)) / 1000 for e in evs]
        out.append(DataFlow(src, dst, len(evs), mean(secs), sum(int(e.payload.get("chars", 0)) for e in evs)))
    return sorted(out, key=lambda f: (-f.count, f.source_app, f.target_app))


def _norm(x: float, hi: float) -> float:
    return 0.0 if hi <= 0 else min(1.0, x / hi)


def _transfer_volume(s: Step) -> float:
    return s.n_pastes + s.n_copies + s.n_transfers + s.n_keys / 10


def score_automation(steps: list[Step]) -> list[AutomationScore]:
    if not steps:
        return []
    by_act: dict[str, list[Step]] = defaultdict(list)
    switches: dict[str, int] = defaultdict(int)
    for i, s in enumerate(steps):
        by_act[s.activity].append(s)
        if i > 0 and steps[i - 1].session_id == s.session_id and steps[i - 1].app != s.app:
            switches[s.activity] += 1

    total = len(steps)
    max_transfer = max((mean(_transfer_volume(x) for x in v) for v in by_act.values()), default=1.0)
    out = []
    for act, v in by_act.items():
        durs = [x.duration_s for x in v]
        m = mean(durs)
        cv = pstdev(durs) / m if m > 0 and len(durs) > 1 else 0.0
        frequency = len(v) / total
        regularity = 1.0 / (1.0 + cv)
        data_transfer = _norm(mean(_transfer_volume(x) for x in v), max_transfer)
        app_switch = switches[act] / len(v)
        score = 0.3 * frequency + 0.2 * regularity + 0.3 * data_transfer + 0.2 * app_switch
        out.append(AutomationScore(act, score, frequency, regularity, data_transfer, app_switch, sum(durs) / 3600))
    return sorted(out, key=lambda a: -a.score)
