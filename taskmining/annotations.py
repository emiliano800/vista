"""Human annotation stage.

Recorders only see screens. Phone calls, paper records, meetings and
whiteboards are invisible, and regex rules mislabel unfamiliar screens. An
``Annotation`` lets an employee or analyst state what happened in a time
range; this stage merges those statements into the step list:

* steps fully inside an annotation are relabelled (``activity_source=HUMAN``);
* steps straddling an annotation boundary are split so the labelled part is
  exact;
* annotated time not covered by any recorded step becomes an off-screen
  ``Step`` (``app="(off-screen)"``) so paper/phone work shows up in the log
  instead of as idle time;
* annotations may also assert a ``case_id``, which wins over inference.

``open_questions`` produces the inverse: time the pipeline could not explain,
phrased as prompts for a guided interview.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import TextIO

from taskmining.models import OFF_SCREEN_APP, Annotation, Source, Step
from taskmining.preprocess import redact_text


def read_annotations(fp: TextIO, fmt: str = "jsonl") -> list[Annotation]:
    if fmt == "csv":
        return [Annotation.from_dict(row) for row in csv.DictReader(fp)]
    return [Annotation.from_dict(json.loads(line)) for line in fp if line.strip()]


def write_annotations(annotations: list[Annotation], fp: TextIO) -> None:
    for a in annotations:
        fp.write(a.to_json())
        fp.write("\n")


def _scale(step: Step, start: datetime, end: datetime) -> Step:
    """Copy ``step`` clipped to [start, end] with counters split pro rata."""
    total = step.duration_s
    frac = ((end - start).total_seconds() / total) if total > 0 else 1.0
    return replace(
        step,
        start=start,
        end=end,
        n_events=max(1, round(step.n_events * frac)),
        n_keys=round(step.n_keys * frac),
        n_copies=round(step.n_copies * frac),
        n_pastes=round(step.n_pastes * frac),
        n_transfers=round(step.n_transfers * frac),
    )


def _split(step: Step, cuts: list[datetime]) -> list[Step]:
    bounds = [step.start] + sorted({c for c in cuts if step.start < c < step.end}) + [step.end]
    if len(bounds) == 2:
        return [step]
    return [_scale(step, a, b) for a, b in zip(bounds, bounds[1:], strict=False)]


def _label(step: Step, a: Annotation) -> None:
    step.activity = a.label
    step.activity_source = Source.HUMAN
    step.note = redact_text(a.note)
    if a.case_id:
        step.case_id = a.case_id
        step.case_source = Source.HUMAN


def apply_annotations(
    steps: list[Step],
    annotations: list[Annotation],
    min_offscreen: timedelta = timedelta(seconds=30),
) -> list[Step]:
    if not annotations:
        return steps
    by_user: dict[str, list[Annotation]] = {}
    for a in annotations:
        by_user.setdefault(a.user, []).append(a)

    out: list[Step] = []
    covered: dict[int, list[tuple[datetime, datetime]]] = {i: [] for i in range(len(annotations))}
    index = {id(a): i for i, a in enumerate(annotations)}

    for s in steps:
        anns = by_user.get(s.user, [])
        cuts = [t for a in anns for t in (a.start, a.end)]
        for piece in _split(s, cuts):
            for a in anns:
                if a.start <= piece.start and piece.end <= a.end:
                    _label(piece, a)
                    covered[index[id(a)]].append((piece.start, piece.end))
                    break
            out.append(piece)

    # session lookup for off-screen steps: reuse the user's session active around the annotation
    sessions: dict[str, list[Step]] = {}
    for s in steps:
        sessions.setdefault(s.user, []).append(s)

    for i, a in enumerate(annotations):
        for gap_start, gap_end in _gaps(a.start, a.end, covered[i]):
            if gap_end - gap_start < min_offscreen:
                continue
            sid = _nearest_session(sessions.get(a.user, []), gap_start, gap_end) or f"{a.user}#annotated"
            off = Step(
                start=gap_start,
                end=gap_end,
                user=a.user,
                session_id=sid,
                app=OFF_SCREEN_APP,
                activity=a.label,
                window_title="",
                n_events=0,
                activity_source=Source.HUMAN,
            )
            _label(off, a)
            out.append(off)

    out.sort(key=lambda s: (s.start, s.user))
    return out


def _gaps(start: datetime, end: datetime, covered: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    gaps = []
    cur = start
    for a, b in sorted(covered):
        if a > cur:
            gaps.append((cur, a))
        cur = max(cur, b)
    if cur < end:
        gaps.append((cur, end))
    return gaps


def _nearest_session(steps: list[Step], start: datetime, end: datetime) -> str | None:
    best: tuple[float, str] | None = None
    for s in steps:
        d = max(0.0, (s.start - end).total_seconds(), (start - s.end).total_seconds())
        if best is None or d < best[0]:
            best = (d, s.session_id)
    return best[1] if best else None


@dataclass
class Question:
    """A prompt for the guided-interview agent about unexplained time."""

    user: str
    start: datetime
    end: datetime
    kind: str  # "unlabelled" | "gap"
    prompt: str

    def to_dict(self) -> dict:
        return {
            "user": self.user,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "kind": self.kind,
            "prompt": self.prompt,
        }


def open_questions(
    steps: list[Step],
    min_gap: timedelta = timedelta(minutes=15),
    max_gap: timedelta = timedelta(hours=4),
) -> list[Question]:
    qs: list[Question] = []
    by_user: dict[str, list[Step]] = {}
    for s in steps:
        by_user.setdefault(s.user, []).append(s)
    for user, seq in by_user.items():
        seq = sorted(seq, key=lambda s: s.start)
        for s in seq:
            if s.activity_source == Source.FALLBACK:
                qs.append(
                    Question(
                        user,
                        s.start,
                        s.end,
                        "unlabelled",
                        f"What were you doing in {s.app} ({s.window_title!r}) at {s.start:%H:%M} on {s.start:%Y-%m-%d}?",
                    )
                )
        for prev, nxt in zip(seq, seq[1:], strict=False):
            gap = nxt.start - prev.end
            if min_gap <= gap <= max_gap:
                qs.append(
                    Question(
                        user,
                        prev.end,
                        nxt.start,
                        "gap",
                        f"No screen activity between {prev.end:%H:%M} and {nxt.start:%H:%M} on {prev.end:%Y-%m-%d} "
                        f"(after '{prev.activity}'). What happened during that time?",
                    )
                )
    qs.sort(key=lambda q: (q.start, q.user))
    return qs
