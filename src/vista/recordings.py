"""Validate the portable report format; never accept desktop file paths or media."""

import csv
import io
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from taskmining.eventlog import CSV_COLUMNS

Count = Annotated[int, Field(ge=0, le=1_000_000_000)]
Amount = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Text = Annotated[str, Field(max_length=4096)]


class AppTime(BaseModel):
    app: Text
    seconds: Amount


class Manifest(BaseModel):
    recording_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    started_at: AwareDatetime
    ended_at: AwareDatetime
    active_seconds: Count
    processing: Literal["done"]
    counts: dict[str, Count] = Field(default_factory=dict, max_length=30)
    apps: list[AppTime] = Field(default_factory=list, max_length=1000)
    # Real user names, local paths, screen filenames and capture settings are omitted.

    @model_validator(mode="after")
    def completed(self):
        if self.ended_at < self.started_at:
            raise ValueError("Session ends before it starts")
        if self.active_seconds > (self.ended_at - self.started_at).total_seconds() + 1:
            raise ValueError("Active time exceeds session duration")
        return self


class Activity(BaseModel):
    activity: Text
    count: Count
    total_s: Amount
    mean_s: Amount
    n_cases: Count


class Variant(BaseModel):
    count: Count
    mean_throughput_s: Amount
    activities: list[Text] = Field(max_length=10000)


class Candidate(BaseModel):
    activity: Text
    score: float = Field(ge=0, le=1, allow_inf_nan=False)
    hours_total: Amount


class DataFlow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    source: Text = Field(alias="from")
    target: Text = Field(alias="to")
    count: Count
    mean_transfer_s: Amount
    chars: Count = 0


class Summary(BaseModel):
    n_raw_events: Count
    n_clean_events: Count
    n_steps: Count
    n_cases: Count
    n_uncorrelated_steps: Count = 0
    n_annotations: Count = 0
    n_open_questions: Count = 0
    off_screen_hours: Amount = 0
    provenance: dict[str, dict[str, Count]] = Field(default_factory=dict)
    activities: list[Activity] = Field(default_factory=list, max_length=10000)
    variants: list[Variant] = Field(default_factory=list, max_length=10000)
    automation_potential: list[Candidate] = Field(default_factory=list, max_length=10000)
    data_flows: list[DataFlow] = Field(default_factory=list, max_length=10000)
    rework: dict[str, Count] = Field(default_factory=dict)


def parse_evidence(value: str) -> list[dict]:
    try:
        return _parse_evidence(value)
    except csv.Error as exc:
        raise ValueError("Malformed or oversized evidence CSV") from exc


def _parse_evidence(value: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(value), strict=True)
    if reader.fieldnames != CSV_COLUMNS:
        raise ValueError("Evidence must be the task-mining event_log.csv")
    rows = []
    for row in reader:
        if len(rows) >= 50000 or None in row or any(v is None or len(v) > 8192 for v in row.values()):
            raise ValueError("Invalid or oversized evidence log")
        start, end = datetime.fromisoformat(row["start"]), datetime.fromisoformat(row["end"])
        if start.tzinfo is None or end.tzinfo is None or end < start:
            raise ValueError("Invalid evidence timestamps")
        for key in ("n_events", "n_keys", "n_copies", "n_pastes", "n_transfers"):
            if not 0 <= int(row[key]) <= 1_000_000_000:
                raise ValueError("Invalid evidence counts")
        duration = float(row["duration_s"])
        if not 0 <= duration <= 31536000:
            raise ValueError("Invalid evidence duration")
        if row["activity_source"] not in {"observed", "rule", "fallback", "human"}:
            raise ValueError("Invalid activity provenance")
        if row["case_source"] not in {"", "observed", "filled", "episode", "human"}:
            raise ValueError("Invalid case provenance")
        rows.append(row)
    return rows


class SectionEdit(BaseModel):
    """What the employee typed over a video section: its name and a note."""

    model_config = ConfigDict(extra="forbid")
    name: Text = ""
    note: Text = ""
    edited_at: AwareDatetime | None = None


SectionId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class FileInterval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: AwareDatetime
    end: AwareDatetime
    app: Text = ""


class RecordingDocument(BaseModel):
    """A document on screen during the session: when it was open (video markers)
    and the snapshot of its last version that ships as media under files/."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-f0-9]{12}$")
    name: str = Field(max_length=255)
    ext: str = Field(max_length=16, pattern=r"^(\.[a-z0-9]{1,15})?$")
    folder: str = Field(max_length=255, default="")
    first_opened: AwareDatetime
    last_closed: AwareDatetime
    seconds: Count = 0
    intervals: list[FileInterval] = Field(default_factory=list, max_length=500)
    used_at: list[AwareDatetime] = Field(default_factory=list, max_length=500)
    sources: list[Literal["ax", "lsof", "spotlight", "download"]] = Field(default_factory=list, max_length=4)
    snapshot: str | None = Field(default=None, max_length=255, pattern=r"^files/[a-f0-9]{12}/[A-Za-z0-9][A-Za-z0-9._-]*$")
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    size_bytes: Count | None = None
    modified_at: AwareDatetime | None = None
    edited: bool = False
    content_type: str | None = Field(default=None, max_length=128, pattern=r"^[a-z]+/[a-z0-9.+-]+$")

    @model_validator(mode="after")
    def ordered(self):
        if self.last_closed < self.first_opened or any(iv.end < iv.start for iv in self.intervals):
            raise ValueError("Document closes before it opens")
        if self.snapshot and not self.snapshot.startswith(f"files/{self.id}/"):
            raise ValueError("Snapshot path does not belong to this document")
        return self


class RecordingUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    manifest: Manifest
    summary: Summary
    event_log_csv: str = Field(max_length=6_000_000)
    name: Text = ""
    summary_text: Text = ""
    sections: dict[SectionId, SectionEdit] = Field(default_factory=dict, max_length=500)
    files: list[RecordingDocument] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def evidence_matches(self):
        rows = parse_evidence(self.event_log_csv)
        if len(rows) != self.summary.n_steps:
            raise ValueError("Summary step count does not match the evidence")
        if len({r["case_id"] for r in rows if r["case_id"]}) != self.summary.n_cases:
            raise ValueError("Summary case count does not match the evidence")
        activities = {r["activity"] for r in rows}
        if any(c.activity not in activities for c in self.summary.automation_potential):
            raise ValueError("Automation candidate has no supporting activity")
        return self
