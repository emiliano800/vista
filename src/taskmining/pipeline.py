from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path

from taskmining import abstraction, analytics, annotations, correlation, discovery, eventlog, preprocess
from taskmining.capture import EventSource
from taskmining.models import Annotation, EventType, RawEvent, Step, write_jsonl

# recording_format 2 telemetry that is not an interaction: it stays in the local log
# for review and graph compilation but must not become steps or inflate counts.
NON_INTERACTION = frozenset({EventType.PATH, EventType.DRAG, EventType.APP_START, EventType.APP_STOP})


@dataclass
class PipelineResult:
    raw: list[RawEvent]
    clean: list[RawEvent]
    steps: list[Step]
    discovery: discovery.DiscoveryResult
    automation: list[analytics.AutomationScore]
    annotations: list[Annotation] = field(default_factory=list)
    questions: list[annotations.Question] = field(default_factory=list)
    data_flows: list[analytics.DataFlow] = field(default_factory=list)

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
        with (out / "annotations.jsonl").open("w") as fp:
            annotations.write_annotations(self.annotations, fp)
        (out / "questions.json").write_text(json.dumps([q.to_dict() for q in self.questions], indent=2, ensure_ascii=False))
        summary = {
            "n_raw_events": len(self.raw),
            "n_clean_events": len(self.clean),
            "n_steps": len(self.steps),
            "n_cases": len(self.discovery.cases),
            "n_uncorrelated_steps": sum(1 for s in self.steps if not s.case_id),
            "n_annotations": len(self.annotations),
            "n_open_questions": len(self.questions),
            "provenance": {
                "activity_source": dict(Counter(s.activity_source.value for s in self.steps)),
                "case_source": dict(Counter(s.case_source.value if s.case_source else "none" for s in self.steps)),
            },
            "off_screen_hours": round(sum(s.duration_s for s in self.steps if s.app == annotations.OFF_SCREEN_APP) / 3600, 3),
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
            "data_flows": [f.to_dict() for f in self.data_flows],
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
        episode_fallback: bool = True,
        redact: bool = True,
    ):
        self.rules = rules
        self.idle_gap = idle_gap
        self.key_gap = key_gap
        self.pseudonymize = pseudonymize
        self.episode_fallback = episode_fallback
        self.redact = redact

    def run(self, source: EventSource, human: list[Annotation] | None = None) -> PipelineResult:
        raw = [e for e in source.events() if e.event_type not in NON_INTERACTION]
        human = list(human or [])
        clean = preprocess.redact(raw) if self.redact else list(raw)
        if self.redact:
            human = [replace(a, note=preprocess.redact_text(a.note)) for a in human]
        if self.pseudonymize:
            clean = preprocess.pseudonymize_users(clean)
            human = [replace(a, user=preprocess.pseudonym(a.user)) for a in human]
        clean = preprocess.aggregate_keystrokes(clean, self.key_gap)
        sessioned = preprocess.sessionize(clean, self.idle_gap)
        steps = abstraction.abstract(sessioned, self.rules)
        steps = annotations.apply_annotations(steps, human)
        correlation.correlate(steps, episodes=self.episode_fallback)
        disc = discovery.discover(steps)
        auto = analytics.score_automation(steps)
        questions = annotations.open_questions(steps)
        flows = analytics.data_flows(clean)
        return PipelineResult(raw, clean, steps, disc, auto, human, questions, flows)
