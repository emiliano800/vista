from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from taskmining import abstraction, analytics, correlation, discovery, eventlog, preprocess
from taskmining.capture import EventSource
from taskmining.models import RawEvent, Step, write_jsonl


@dataclass
class PipelineResult:
    raw: list[RawEvent]
    clean: list[RawEvent]
    steps: list[Step]
    discovery: discovery.DiscoveryResult
    automation: list[analytics.AutomationScore]

    def write(self, out: Path) -> None:
        out.mkdir(parents=True, exist_ok=True)
        with (out / "raw_events.jsonl").open("w") as fp:
            write_jsonl(self.raw, fp)
        with (out / "clean_events.jsonl").open("w") as fp:
            write_jsonl(self.clean, fp)
        with (out / "event_log.csv").open("w", newline="") as fp:
            eventlog.to_csv(self.steps, fp)
        with (out / "event_log.xes").open("w") as fp:
            eventlog.to_xes(self.steps, fp)
        (out / "dfg.dot").write_text(self.discovery.to_dot())
        (out / "dfg.mmd").write_text(self.discovery.to_mermaid())
        summary = {
            "n_raw_events": len(self.raw),
            "n_clean_events": len(self.clean),
            "n_steps": len(self.steps),
            "n_cases": len(self.discovery.cases),
            "n_uncorrelated_steps": sum(1 for s in self.steps if not s.case_id),
            "variants": [
                {"count": v.count, "mean_throughput_s": round(v.mean_throughput_s, 1), "activities": list(v.activities)}
                for v in self.discovery.variants
            ],
            "activities": [
                {
                    "activity": a.activity,
                    "count": a.count,
                    "total_s": round(a.total_s, 1),
                    "mean_s": round(a.mean_s, 2),
                    "n_cases": a.n_cases,
                }
                for a in self.discovery.activities
            ],
            "rework": dict(self.discovery.rework),
            "automation_potential": [
                {"activity": a.activity, "score": round(a.score, 3), "hours_total": round(a.hours_total, 3)} for a in self.automation
            ],
        }
        (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))


class Pipeline:
    def __init__(
        self,
        rules: list[abstraction.ActivityRule] | None = None,
        idle_gap: timedelta = timedelta(minutes=10),
        key_gap: timedelta = timedelta(seconds=2),
        pseudonymize: bool = True,
    ):
        self.rules = rules
        self.idle_gap = idle_gap
        self.key_gap = key_gap
        self.pseudonymize = pseudonymize

    def run(self, source: EventSource) -> PipelineResult:
        raw = list(source.events())
        clean = preprocess.redact(raw)
        if self.pseudonymize:
            clean = preprocess.pseudonymize_users(clean)
        clean = preprocess.aggregate_keystrokes(clean, self.key_gap)
        sessioned = preprocess.sessionize(clean, self.idle_gap)
        steps = abstraction.abstract(sessioned, self.rules)
        correlation.correlate(steps)
        disc = discovery.discover(steps)
        auto = analytics.score_automation(steps)
        return PipelineResult(raw, clean, steps, disc, auto)
