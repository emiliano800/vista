"""Metadata-only recorder analysis: deterministic facts, questions, and the
guard rails around the model's interpretation. No database, no model."""

from vista.recorder_analysis import (
    MAX_QUESTIONS,
    ModelOutput,
    apply_interpretation,
    coverage_for,
    deterministic_questions,
    merge_questions,
    observe,
    parse,
)

MANIFEST = {"started_at": "2026-09-19T09:00:00Z", "ended_at": "2026-09-19T10:00:00Z", "active_seconds": 3000}


def event(minute: int, app: str, event_type: str = "click", second: int = 0, count: int = 1) -> dict:
    return {"timestamp": f"2026-09-19T09:{minute:02d}:{second:02d}Z", "event_type": event_type, "app": app, "count": count}


def test_observe_derives_spans_switches_transfers_loops_and_stretches():
    events = [
        event(0, "Excel", "key", count=40),
        event(1, "Excel", "copy"),
        event(1, "Browser", "paste", second=20),
        event(2, "Browser", "click"),
        event(3, "Excel", "copy"),
        event(3, "Browser", "paste", second=30),
        event(4, "Excel", "click"),
        event(5, "Excel", "paste"),  # same app: internal paste, not a transfer
        # idle for 10 minutes → a new stretch, and the Excel span ends at 09:05
        event(15, "Mail", "click"),
        event(16, "Mail", "key", count=12),
        event(17, "Excel", "click"),  # copy from Excel 14 minutes ago is too old to count
        event(17, "Excel", "paste", second=30),
    ]
    observed = observe(events, MANIFEST)
    assert observed["events"] == 40 + 12 + 10
    apps = {a["app"]: a for a in observed["apps"]}
    assert set(apps) == {"Excel", "Browser", "Mail"}
    assert apps["Excel"]["keys"] == 40 and apps["Excel"]["copies"] == 2 and apps["Excel"]["pastes"] == 2
    assert apps["Browser"]["pastes"] == 2
    assert observed["apps"][0]["app"] == "Excel" and observed["apps"][0]["share"] > observed["apps"][1]["share"]
    assert observed["switches"] == 6
    assert observed["transfers"] == [{"from": "Excel", "to": "Browser", "count": 2, "mean_latency_s": 25.0}]
    assert observed["internal_pastes"] == 1
    assert observed["loops"] == [{"between": ["Browser", "Excel"], "count": 4}, {"between": ["Excel", "Mail"], "count": 2}]
    assert [s["id"] for s in observed["stretches"]] == ["s1", "s2"]
    assert observed["stretches"][0]["switches"] == 4 and observed["stretches"][0]["apps"][0] == "Excel"
    assert observed["stretches"][1]["apps"] == ["Mail", "Excel"]
    assert observed["session"] == MANIFEST
    # Idle time is not active time: the first stretch's Excel span ends at its last event.
    assert sum(a["active_s"] for a in observed["apps"]) <= 8 * 60


def test_observe_handles_an_empty_or_single_event_session():
    empty = observe([], MANIFEST)
    assert empty["events"] == 0 and empty["apps"] == [] and empty["stretches"] == [] and empty["switches"] == 0
    single = observe([event(0, "Mail")], MANIFEST)
    assert single["apps"][0]["app"] == "Mail" and single["stretches"][0]["duration_s"] == 0


def test_questions_are_anchored_to_observed_facts_and_bounded():
    busy = [event(0, "Excel")] + [event(m, "Browser" if m % 2 else "Excel", "click") for m in range(1, 9)]
    busy += [event(10, "Excel", "copy"), event(10, "Browser", "paste", second=10)]
    observed = observe(busy, MANIFEST)
    questions = deterministic_questions(observed)
    assert 1 <= len(questions) <= MAX_QUESTIONS
    assert questions[0]["about"]["stretch"] == "s1" and set(questions[0]["about"]["apps"]) == {"Excel", "Browser"}
    assert "09:00" in questions[0]["question"] and "moved between" in questions[0]["question"]
    assert any(q["about"].get("transfer") == ["Excel", "Browser"] for q in questions)
    assert all(q["source"] == "observed" and q["id"].startswith("q") for q in questions)
    assert deterministic_questions(observe([event(0, "Mail")], MANIFEST)) == [
        {
            "id": "q1",
            "source": "observed",
            "question": (
                "Most of the session (100%) was in Mail. Which task were you doing there, and is it something you repeat every day or week?"
            ),
            "about": {"apps": ["Mail"]},
        }
    ]


def test_model_output_is_parsed_tolerantly_and_filtered_to_observed_apps():
    observed = observe([event(0, "Excel"), event(1, "Browser")], MANIFEST)
    assert parse("") is None and parse("not json") is None and parse('{"workflows": "nope"}') is None
    output = parse(
        '```json\n{"summary": "Re-keying between a workbook and a portal.", '
        '"workflows": [{"name": "Portal entry", "apps": ["excel", "Browser"], "evidence": "6 transfers", "confidence": 0.7}, '
        '{"name": "Invented", "apps": ["SAP"], "confidence": 0.9}], '
        '"automation_candidates": [{"title": "Bulk upload", "rationale": "…", "apps": ["Browser"], "confidence": 0.4}], '
        '"questions": [{"question": "Which portal is this?", "apps": ["Browser"]}, {"question": "Is SAP involved?", "apps": ["SAP"]}]}\n```'
    )
    assert isinstance(output, ModelOutput)
    interpretation, questions = apply_interpretation(output, observed, model="gpt-6-astra", source="live")
    assert interpretation["summary"].startswith("Re-keying")
    assert [w["name"] for w in interpretation["workflows"]] == ["Portal entry"]
    assert interpretation["workflows"][0]["apps"] == ["Excel", "Browser"]  # canonical spelling from observed facts
    assert [c["title"] for c in interpretation["automation_candidates"]] == ["Bulk upload"]
    assert interpretation["rejected"] == 2
    assert questions == [{"source": "model", "question": "Which portal is this?", "about": {"apps": ["Browser"]}}]
    stub, extra = apply_interpretation(None, observed, model="stub-model-v0", source="stub")
    assert stub == {"source": "stub", "model": "stub-model-v0", "summary": "", "workflows": [], "automation_candidates": [], "rejected": 0}
    assert extra == []


def test_merge_questions_dedupes_and_caps():
    deterministic = [{"id": f"q{i}", "source": "observed", "question": f"Question {i}?", "about": {}} for i in range(1, 5)]
    model = [
        {"source": "model", "question": "question 2?", "about": {"apps": []}},
        {"source": "model", "question": "New one?", "about": {"apps": []}},
        {"source": "model", "question": "Another?", "about": {"apps": []}},
        {"source": "model", "question": "Too many?", "about": {"apps": []}},
    ]
    merged = merge_questions(deterministic, model)
    assert [q["id"] for q in merged] == ["q1", "q2", "q3", "q4", "q5", "q6"]
    assert [q["question"] for q in merged[4:]] == ["New one?", "Another?"]
    assert all(q["answer"] is None and q["answered_at"] is None for q in merged)


def test_coverage_states_what_was_not_observable():
    coverage = coverage_for(observe([event(0, "Excel")], MANIFEST), document_count=1)
    assert coverage["fields"] == ["timestamp", "event_type", "app", "count"]
    assert {"window_title", "url", "typed_text", "clipboard", "screenshots"} <= set(coverage["excluded"])
    assert coverage["documents"] == 1 and coverage["apps"] == 1
