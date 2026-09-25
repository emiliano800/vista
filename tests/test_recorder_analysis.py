"""Metadata-only recorder analysis: deterministic facts, questions, and the
guard rails around the model's interpretation. No database, no model."""

import json

import pytest

from vista.agents.jev import NONE, Judgment
from vista.automation.schemas import WorkflowDefinition
from vista.config import settings
from vista.recorder_analysis import (
    AUTOMATION_THRESHOLD,
    KIND_NAMES,
    MAX_QUESTIONS,
    ModelOutput,
    apply_interpretation,
    apply_judgment,
    coverage_for,
    deterministic_questions,
    draft_definition,
    finding_rows,
    has_detail,
    instructions_for,
    interpret,
    interpret_with_jev,
    judge_request,
    merge_questions,
    observe,
    parse,
    workflow_candidates,
)

MANIFEST = {"started_at": "2026-09-19T09:00:00Z", "ended_at": "2026-09-19T10:00:00Z", "active_seconds": 3000}


def event(minute: int, app: str, event_type: str = "click", second: int = 0, count: int = 1) -> dict:
    return {"timestamp": f"2026-09-19T09:{minute:02d}:{second:02d}Z", "event_type": event_type, "app": app, "count": count}


def busy_events() -> list[dict]:
    """Two stretches of work: Excel↔Browser copying for 2½ minutes, then after 12 idle minutes a
    short Mail↔Excel visit. Every gap inside a stretch is at most 30 s."""
    return [
        event(0, "Excel", "key", count=40),
        event(0, "Excel", "copy", second=20),
        event(0, "Browser", "paste", second=40),
        event(1, "Browser", "click"),
        event(1, "Excel", "copy", second=20),
        event(1, "Browser", "paste", second=50),
        event(2, "Excel", "click", second=10),
        event(2, "Excel", "paste", second=30),  # same app: an internal paste, not a transfer between apps
        # idle for 12 minutes → a new stretch, and the Excel span ends at 09:02:30
        event(15, "Mail", "click"),
        event(15, "Mail", "key", second=20, count=12),
        event(15, "Excel", "click", second=40),  # copy from Excel 14 minutes ago is too old to count
        event(16, "Excel", "paste"),
    ]


def test_observe_derives_spans_switches_transfers_loops_and_stretches():
    observed = observe(busy_events(), MANIFEST)
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
    # The tasks inside each stretch: every copy→paste (the internal one too), every application typed into.
    assert observed["stretches"][0]["tasks"] == [
        {"kind": "transfer", "from": "Excel", "to": "Browser", "count": 2},
        {"kind": "transfer", "from": "Excel", "to": "Excel", "count": 1},
        {"kind": "entry", "app": "Excel", "count": 40},
    ]
    assert observed["stretches"][1]["tasks"] == [{"kind": "entry", "app": "Mail", "count": 12}]
    assert observed["session"] == MANIFEST
    # Idle time is not active time: the first stretch's Excel span ends at its last event.
    assert sum(a["active_s"] for a in observed["apps"]) <= 8 * 60


def test_observe_handles_an_empty_or_single_event_session():
    empty = observe([], MANIFEST)
    assert empty["events"] == 0 and empty["apps"] == [] and empty["stretches"] == [] and empty["switches"] == 0
    single = observe([event(0, "Mail")], MANIFEST)
    assert single["apps"][0]["app"] == "Mail" and single["stretches"][0]["duration_s"] == 0
    assert single["stretches"][0]["tasks"] == [{"kind": "activity", "app": "Mail", "count": 1}]


def test_a_gap_over_thirty_seconds_starts_a_new_stretch():
    close = observe([event(0, "Excel"), event(0, "Excel", second=30)], MANIFEST)
    apart = observe([event(0, "Excel"), event(0, "Excel", second=31)], MANIFEST)
    assert len(close["stretches"]) == 1 and len(apart["stretches"]) == 2


def test_questions_are_anchored_to_observed_facts_and_bounded():
    # Eight switches 20 s apart, then a transfer 20 s later: one stretch, since no gap exceeds 30 s.
    busy = [event(0, "Excel")] + [event(m // 3, "Browser" if m % 2 else "Excel", "click", second=m % 3 * 20) for m in range(1, 9)]
    busy += [event(3, "Excel", "copy"), event(3, "Browser", "paste", second=10)]
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


# ---- Jev path: candidates from code, typed judgments, templated interpretation ----


def busy_session():
    return observe(busy_events(), MANIFEST)


def test_workflow_candidates_are_every_stretch_of_work_with_its_tasks():
    candidates = workflow_candidates(busy_session())
    # One candidate per stretch: the Excel↔Browser copying, then the short Mail visit. Nothing needs two apps.
    assert [(c["id"], c["pattern"], c["apps"], c["count"]) for c in candidates] == [
        ("c1", "transfer", ["Excel", "Browser"], 1),
        ("c2", "entry", ["Mail", "Excel"], 1),
    ]
    c1 = candidates[0]
    assert [t["name"] for t in c1["tasks"]] == [
        "Copy and paste Excel → Browser 2×",
        "Copy and paste within Excel",
        "Type into Excel (40 keystrokes)",
    ]
    assert c1["about"]["stretches"] == ["s1"] and c1["about"]["switches"] == 4
    assert c1["about"]["tasks"][0] == {"kind": "transfer", "from": "Excel", "to": "Browser", "count": 2}
    assert c1["evidence"] == (
        "Copy and paste Excel → Browser 2×; Copy and paste within Excel; Type into Excel (40 keystrokes) between 09:00 and 09:02"
    )
    assert candidates[1]["evidence"] == "Type into Mail (12 keystrokes) between 09:15 and 09:16"


def test_a_lone_click_in_one_app_is_still_a_candidate_and_recurring_stretches_merge():
    lone = workflow_candidates(observe([event(0, "Mail")], MANIFEST))
    assert [(c["pattern"], c["apps"], c["count"], c["tasks"][0]["name"]) for c in lone] == [
        ("activity", ["Mail"], 1, "Work in Mail (1 click)")
    ]
    # The same Docs → Excel copy in three separate stretches is one candidate seen three times — that is what recurring means.
    events = []
    for m in (0, 5, 10):
        events += [event(m, "Docs", "copy"), event(m, "Excel", "paste", second=15)]
    repeated = workflow_candidates(observe(events, MANIFEST))
    assert len(repeated) == 1 and repeated[0]["count"] == 3 and repeated[0]["apps"] == ["Docs", "Excel"]
    assert repeated[0]["tasks"] == [
        {"kind": "transfer", "from": "Docs", "to": "Excel", "count": 3, "name": "Copy and paste Docs → Excel 3×"}
    ]
    assert repeated[0]["evidence"].endswith("between 09:00 and 09:10, seen in 3 stretches of work")


def test_judge_request_asks_four_typed_questions_per_candidate_over_the_facts():
    observed = busy_session()
    candidates = workflow_candidates(observed)
    state, questions = judge_request(observed, candidates, [{"filename": "ar.xlsx", "summary": "workbook", "excerpt": "Invoice,Total"}])
    assert [c["id"] for c in state["candidates"]] == ["c1", "c2"] and "about" not in state["candidates"][0]
    assert state["candidates"][0]["tasks"][0] == "Copy and paste Excel → Browser 2×"
    assert state["observed"]["transfers"] == observed["transfers"] and state["shared_documents"][0]["filename"] == "ar.xlsx"
    assert len(questions) == 4 * len(candidates)
    assert {questions[f"c1_{q}"]["type"] for q in ("workflow", "ask")} == {"noul"}
    assert questions["c1_kind"]["type"] == "choice" and NONE in questions["c1_kind"]["criteria"]
    assert questions["c1_mechanical"]["type"] == "score" and len(questions["c1_mechanical"]["criteria"]) == 4
    assert "Excel and Browser" in questions["c1_workflow"]["instructions"]
    # A one-application candidate is only offered the kinds that fit inside one application.
    _, lone = judge_request(observed, workflow_candidates(observe([event(0, "Mail", "key", count=5)], MANIFEST)), [])
    assert set(lone["c1_kind"]["criteria"]) == {"data_entry", "document_work", NONE}


def answers(cid, workflow, kind, p_kind, mechanical, p_mech, ask):
    probs = {k: 0.0 for k in (*KIND_NAMES, NONE)}
    probs[kind] = p_kind
    return {
        f"{cid}_workflow": {"type": "noul", "noul": workflow},
        f"{cid}_kind": {"type": "choice", "choice": kind, "confidence": p_kind, "probabilities": probs},
        f"{cid}_mechanical": {"type": "score", "score": mechanical, "confidence": p_mech, "legend": {}, "probabilities": {}},
        f"{cid}_ask": {"type": "noul", "noul": ask},
    }


def judged():
    """c1 (Excel→Browser copying): a mechanical data transfer, nothing to ask.
    c2 (Mail, then Excel): a communication loop, not mechanical, only the employee can say what it is."""
    return Judgment(
        model="jev-1.13.0",
        input_tokens=900,
        answers={
            **answers("c1", 0.88, "data_transfer", 0.95, 2.7, 0.83, 0.2),
            **answers("c2", 0.92, "communication", 0.81, 1.4, 0.6, 0.85),
        },
    )


def test_apply_judgment_is_templates_over_typed_answers():
    candidates = workflow_candidates(busy_session())
    interpretation, questions = apply_judgment(judged(), candidates, model="jev-1.13.0", source="live")
    assert interpretation["source"] == "live" and interpretation["model"] == "jev-1.13.0"
    assert interpretation["rejected"] == 0 and interpretation["unsure"] == 0 and interpretation["judged"] == 2
    assert [w["name"] for w in interpretation["workflows"]] == ["Data transfer: Excel → Browser", "Communication loop: Mail ↔ Excel"]
    assert [w["status"] for w in interpretation["workflows"]] == ["likely", "likely"]
    assert interpretation["workflows"][0]["confidence"] == 0.88  # min(p_workflow, p_kind)
    assert interpretation["workflows"][0]["tasks"][0] == "Copy and paste Excel → Browser 2×"
    assert interpretation["workflows"][0]["about"]["tasks"][0]["to"] == "Browser"
    auto = interpretation["automation_candidates"]
    assert [a["title"] for a in auto] == ["Automate data transfer: Excel → Browser"]
    assert (
        auto[0]["mechanical"] == 2.7 and auto[0]["confidence"] == 0.83 and auto[0]["rationale"].startswith("The same mechanical sequence")
    )
    assert auto[0]["mechanical"] >= AUTOMATION_THRESHOLD > 1.4
    assert questions == [
        {
            "source": "model",
            "question": (
                "Around your work in Excel, you kept returning to Mail. "
                "What are you sending or receiving there, and who decides what happens next?"
            ),
            "about": {"apps": ["Mail", "Excel"], "candidate": "c2"},
        }
    ]
    assert (
        interpretation["summary"]
        == "2 workflows from 2 stretches of work; 2 judged likely recurring; 1 looks mechanical enough to automate."
    )


def test_low_confidence_and_no_kind_are_labels_not_filters():
    """Jev doubting c1 is recurring, and finding no kind for c2, changes their status — both stay on the report."""
    candidates = workflow_candidates(busy_session())
    doubted = Judgment(
        model="jev-1.13.0",
        answers={**answers("c1", 0.30, "data_transfer", 0.9, 2.9, 0.9, 0.2), **answers("c2", 0.80, NONE, 0.7, 2.9, 0.9, 0.2)},
    )
    interpretation, questions = apply_judgment(doubted, candidates, model="jev-1.13.0", source="live")
    unsure, unclear = interpretation["workflows"]
    assert interpretation["rejected"] == 0 and interpretation["unsure"] == 2
    assert unsure["name"] == "Data transfer: Excel → Browser" and unsure["status"] == "unsure" and unsure["confidence"] == 0.3
    # Mechanical enough is still mechanical enough: the draft is offered, badged unsure, for the FDE to decide.
    assert interpretation["automation_candidates"][0]["candidate"] == "c1" and unsure["actions"]["draft_definition"] is not None
    assert unclear["name"] == "Unclear work: Mail, Excel" and unclear["kind"] == NONE and unclear["status"] == "unsure"
    assert unclear["tasks"] == ["Type into Mail (12 keystrokes)"]
    # No kind → nothing to automate and always something to ask, whatever p_ask said.
    assert unclear["actions"]["draft_definition"] is None and len(interpretation["automation_candidates"]) == 1
    assert questions == [
        {
            "source": "model",
            "question": "You worked in Mail, Excel between 09:15 and 09:16. What were you doing, and is it something you repeat?",
            "about": {"apps": ["Mail", "Excel"], "candidate": "c2"},
        }
    ]
    assert (
        "too little to classify" in unclear["actions"]["instructions"][1]
        and "Nothing to automate yet" in unclear["actions"]["instructions"][2]
    )
    assert (
        interpretation["summary"]
        == "2 workflows from 2 stretches of work; 0 judged likely recurring; 1 looks mechanical enough to automate."
    )


def test_jev_stub_shows_every_stretch_as_unclear_and_proposes_nothing(monkeypatch):
    monkeypatch.setattr(settings, "typesafe_api_key", None)
    monkeypatch.delenv("VISTA_JEV_CASSETTE", raising=False)
    result = interpret_with_jev(busy_session(), [])
    assert result.source == "stub" and result.model == "stub-jev-v0" and result.input_tokens > 0
    assert [(w["kind"], w["status"]) for w in result.interpretation["workflows"]] == [(NONE, "unsure")] * 2
    assert result.interpretation["automation_candidates"] == [] and result.interpretation["unsure"] == 2
    assert [q["about"]["candidate"] for q in result.questions] == ["c1", "c2"]
    assert result.event == {"interpreter": "jev", "candidates": 2, "questions": 8, "rejected": 0, "unsure": 2, "employee_answers": 0}
    quiet = interpret_with_jev(observe([], MANIFEST), [], judge_fn=lambda *a: pytest.fail("must not call the model"))
    assert quiet.source == "code" and quiet.input_tokens == 0 and quiet.interpretation["summary"] == ""


def test_interpreter_setting_selects_jev_or_chat(monkeypatch):
    monkeypatch.setattr(settings, "typesafe_api_key", None)
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.delenv("VISTA_JEV_CASSETTE", raising=False)
    monkeypatch.delenv("VISTA_LLM_CASSETTE", raising=False)
    monkeypatch.setattr(settings, "recorder_interpreter", "jev")
    assert interpret(busy_session(), []).event["interpreter"] == "jev"
    monkeypatch.setattr(settings, "recorder_interpreter", "chat")
    chat_result = interpret(busy_session(), [])
    assert chat_result.event["interpreter"] == "chat" and chat_result.model == "stub-model-v0"


# ---- actionable output: instructions, prefilled drafts, findings at publish ----------


def test_every_kind_yields_a_valid_prefilled_workflow_definition():
    c = workflow_candidates(busy_session())[0]  # the Excel → Browser copying
    docs = [{"filename": "ar_aging.xlsx", "summary": {"kind": "workbook"}, "excerpt": ""}]
    for kind in KIND_NAMES:
        definition = WorkflowDefinition.model_validate(draft_definition(kind, c, docs))  # raises on any schema violation
        assert definition.environment == "sandbox" and definition.limits.max_steps == 10
        assert "Excel" in definition.goal and ("Browser" in definition.goal or kind in ("data_entry", "document_work"))
        assert definition.required_inputs[:2] == ["Excel export or sample", "Browser field list"]
        assert "shared document: ar_aging.xlsx" in definition.required_inputs
        assert all(t.islower() and " " not in t for t in definition.allowed_tools)
    assert "2 times per session" in draft_definition("data_transfer", c, [])["goal"]
    # A one-application candidate drafts against that application alone.
    lone = workflow_candidates(observe([event(0, "Excel", "key", count=30)], MANIFEST))[0]
    entry = WorkflowDefinition.model_validate(draft_definition("data_entry", lone, []))
    assert entry.goal.startswith("Enter the values the employee keys into Excel")
    assert entry.required_inputs == ["Excel source values or sample", "Excel field list"]


def test_instructions_are_specific_and_end_in_a_draft_or_a_smaller_fix():
    c = workflow_candidates(busy_session())[0]
    with_docs = instructions_for("data_transfer", c, [{"filename": "ar_aging.xlsx"}], "What is re-keyed?", True)
    assert with_docs[0].startswith(
        "Confirm with the employee what actually moves between Excel and Browser — the report asks: “What is re-keyed?”"
    )
    assert "ar_aging.xlsx" in with_docs[1] and "is the Excel source" in with_docs[1]
    assert "map each to a column in the Excel source" in with_docs[2]
    assert "copy and paste Excel → Browser 2×; copy and paste within Excel; type into Excel (40 keystrokes)" in with_docs[3]
    assert with_docs[4].startswith("Draft the workflow from the prefilled definition") and "$1.00 per run" in with_docs[4]
    assert "admin decision" in with_docs[5] and len(with_docs) == 6
    without = instructions_for("reconciliation", c, [], None, False)
    assert "the report asks" not in without[0] and "representative export or sample from Excel" in without[1]
    assert without[-1].startswith("Not mechanical enough to automate yet") and len(without) == 5
    lone = workflow_candidates(observe([event(0, "Excel", "key", count=30)], MANIFEST))[0]
    assert instructions_for("data_entry", lone, [], None, False)[0].startswith("Confirm with the employee what the work in Excel is")
    assert instructions_for(NONE, lone, [], "What was it?", False) == [
        "Confirm with the employee what the work in Excel is — the report asks: “What was it?”. "
        "Their answer on the published report is the baseline; do not proceed from the pattern alone.",
        "The observed tasks — type into Excel (30 keystrokes) — were too little to classify. Once the employee has said what they are, "
        "decide whether they belong to a workflow already on this report or are one of their own, and rename it.",
        "Nothing to automate yet: an unclear task is not a baseline. Revisit after the next recording of the same work.",
    ]


def test_apply_judgment_attaches_actions_only_where_they_apply():
    candidates = workflow_candidates(busy_session())
    docs = [{"filename": "ar_aging.xlsx", "summary": {}, "excerpt": ""}]
    interpretation, questions = apply_judgment(judged(), candidates, model="jev-1.13.0", source="live", docs=docs)
    transfer, mail = interpretation["workflows"]
    assert transfer["actions"]["question"] is None and "the report asks" not in transfer["actions"]["instructions"][0]
    assert WorkflowDefinition.model_validate(transfer["actions"]["draft_definition"]).allowed_tools[0] == "read_source_records"
    assert interpretation["automation_candidates"][0]["actions"] is transfer["actions"]
    assert mail["actions"]["draft_definition"] is None and mail["actions"]["question"] == questions[0]["question"]
    assert "the report asks" in mail["actions"]["instructions"][0] and "ar_aging.xlsx" in mail["actions"]["instructions"][1]


class FakeReport:
    def __init__(self, interpretation, questions, run_id="00000000-0000-0000-0000-00000000aaaa"):
        self.id = "00000000-0000-0000-0000-00000000bbbb"
        self.run_id = run_id
        self.interpretation = interpretation
        self.questions = questions


def test_finding_rows_cite_report_candidate_run_and_carry_answer_and_actions():
    candidates = workflow_candidates(busy_session())
    interpretation, questions = apply_judgment(judged(), candidates, model="jev-1.13.0", source="live")
    questions = [dict(q, id="q9", answer="Vendor statements from the portal", answered_at="2026-09-22T09:30:00Z") for q in questions]
    rows = finding_rows(FakeReport(interpretation, questions))
    assert [(r["kind"], r["title"]) for r in rows] == [
        ("proposed_automation", "Automate data transfer: Excel → Browser"),
        ("inefficiency", "Communication loop: Mail ↔ Excel"),
    ]
    transfer, mail = rows
    assert transfer["evidence"]["refs"] == [
        "report:00000000-0000-0000-0000-00000000bbbb",
        "candidate:c1",
        "run:00000000-0000-0000-0000-00000000aaaa",
    ]
    assert transfer["evidence"]["mechanical"] == 2.7 and transfer["evidence"]["answer"] is None
    assert transfer["evidence"]["actions"]["draft_definition"]["goal"].startswith("Move the values")
    assert "Judged a recurring workflow at 88%; mechanical 2.7 of 3." in transfer["detail"]  # min(p_workflow, p_kind)
    assert "Tasks: Copy and paste Excel → Browser 2×; Copy and paste within Excel; Type into Excel (40 keystrokes)." in transfer["detail"]
    assert transfer["evidence"]["status"] == "likely" and transfer["evidence"]["tasks"][1] == "Copy and paste within Excel"
    assert transfer["finding_type"] == "workflow.data_transfer"
    assert mail["evidence"]["answer"] == "Vendor statements from the portal" and "Employee: Vendor statements" in mail["detail"]
    assert mail["finding_type"] == "workflow.communication" and "not mechanical enough" in mail["detail"]
    assert mail["evidence"]["actions"]["draft_definition"] is None
    # Items from the chat interpreter have no candidate; they still become findings, citing what they can.
    legacy = finding_rows(
        FakeReport(
            {"workflows": [{"name": "Portal entry", "apps": ["Excel", "Portal"], "evidence": "5 transfers", "confidence": 0.7}]},
            [],
            run_id=None,
        )
    )
    assert legacy[0]["kind"] == "inefficiency" and legacy[0]["evidence"]["refs"] == ["report:00000000-0000-0000-0000-00000000bbbb"]
    assert legacy[0]["finding_type"] is None and legacy[0]["evidence"]["actions"] is None
    assert finding_rows(FakeReport({}, [])) == []


def _detail_event(minute: int, app: str, event_type: str = "click", **fields) -> dict:
    return {**event(minute, app, event_type), **fields}


def test_observe_keeps_titles_pages_typed_runs_and_transfer_samples_when_the_upload_carried_them():
    events = [
        _detail_event(0, "Excel", "focus", window_title="Q3 invoices.xlsx"),
        _detail_event(1, "Excel", "copy", window_title="Q3 invoices.xlsx", text="INV-1042  $1,250.00"),
        *[{**_detail_event(1, "Excel", "key", text=ch), "timestamp": f"2026-09-19T09:01:{10 + i:02d}Z"} for i, ch in enumerate("Paid")],
        _detail_event(2, "Browser", "focus", window_title="Portal — Payments", url="https://portal.example/pay?id=9"),
        {
            **_detail_event(2, "Browser", "paste", url="https://portal.example/pay?id=9", text="INV-1042  $1,250.00"),
            "timestamp": "2026-09-19T09:02:30Z",
        },
        _detail_event(3, "Browser", "file", window_title="remittance.pdf"),
    ]
    observed = observe(events, MANIFEST)
    excel = next(a for a in observed["apps"] if a["app"] == "Excel")
    browser = next(a for a in observed["apps"] if a["app"] == "Browser")
    assert excel["titles"] == ["Q3 invoices.xlsx"] and excel["typed"] == ["Paid"]
    assert browser["titles"] == ["Portal — Payments"] and browser["pages"] == ["https://portal.example/pay"]
    assert browser["files"] == ["remittance.pdf"]
    assert observed["transfers"][0]["samples"] == ["INV-1042  $1,250.00"]
    assert has_detail(observed)
    # Metadata-only sessions look exactly as before: no detail keys at all.
    plain = observe([event(0, "Excel"), event(1, "Browser")], MANIFEST)
    assert not has_detail(plain) and "titles" not in plain["apps"][0]


def test_coverage_and_jev_context_follow_the_sharing_policy():
    detailed = observe([_detail_event(0, "Excel", window_title="Q3 invoices.xlsx")], MANIFEST)
    full = coverage_for(detailed, document_count=0, sharing_policy="activity-full-v1")
    assert (
        full["sharing_policy"] == "activity-full-v1" and "window_title" in full["fields"] and full["excluded"] == ["screenshots", "video"]
    )
    metadata = coverage_for(observe([event(0, "Excel")], MANIFEST), document_count=0)
    assert metadata["sharing_policy"] == "activity-metadata-v1" and "window_title" in metadata["excluded"]
    state, _ = judge_request(detailed, [], [])
    assert "titles" in state["context"] and "not shared" not in state["context"]
    state, _ = judge_request(observe([event(0, "Excel")], MANIFEST), [], [])
    assert "not shared" in state["context"]


def test_employee_answers_are_given_to_jev_and_quoted_in_the_summary():
    from vista.agents.jev import stub_answers

    observed = observe(
        [event(0, "Slack"), event(1, "Google Chrome"), event(2, "Slack"), event(3, "Google Chrome"), event(4, "Slack")], MANIFEST
    )
    candidates = workflow_candidates(observed)
    assert candidates, "a Slack↔Chrome loop is a candidate"
    answers = [{"question": "What were you working on?", "answer": "Taking Slack messages and copying them into a Google Doc, every day"}]

    state, questions = judge_request(observed, candidates, [], answers)
    assert state["employee_answers"] == answers
    assert "employee_answers" in state["context"]
    assert all("employee_answers" in q["instructions"] for q in questions.values())
    plain_state, plain_questions = judge_request(observed, candidates, [])
    assert "employee_answers" not in plain_state and all("employee_answers" not in q["instructions"] for q in plain_questions.values())

    seen = []

    def judge_fn(state, questions):
        seen.append(state)
        return Judgment(model="jev-test", source="live", answers=stub_answers(questions), input_tokens=10, output_tokens=0)

    result = interpret_with_jev(observed, [], judge_fn=judge_fn, answers=answers)
    assert seen[0]["employee_answers"] == answers
    assert result.event["employee_answers"] == 1 and result.interpretation["answered"] == 1
    assert "Read with 1 answer from the employee, who described it as: “Taking Slack messages" in result.interpretation["summary"]
    assert interpret_with_jev(observed, [], judge_fn=judge_fn).interpretation["answered"] == 0


def test_jev_sees_typing_as_fields_typed_not_key_volume_and_ranks_candidates_the_same():
    events = [
        event(0, "Excel", "key", count=40),
        event(0, "Excel", "copy", second=10),
        event(0, "Browser", "paste", second=20),
        event(0, "Browser", "click", second=30),
        event(0, "Excel", "copy", second=40),
        event(0, "Browser", "paste", second=50),
        event(1, "Excel", "click"),
        event(1, "Excel", "paste", second=10),
    ]
    light = observe(events, MANIFEST)
    # The same session with two thousand more keystrokes in one continuous run of typing.
    heavy = observe(events + [event(1, "Browser", "key", second=s, count=100) for s in range(11, 31)], MANIFEST)
    browser = lambda o: next(a for a in o["apps"] if a["app"] == "Browser")  # noqa: E731
    assert browser(heavy)["keys"] - browser(light)["keys"] == 2000, "the recording keeps every key"
    assert browser(heavy)["typing_runs"] == browser(light)["typing_runs"] + 1
    assert heavy["stretches"][0]["interactions"] == light["stretches"][0]["interactions"] + 1
    assert heavy["stretches"][0]["events"] == light["stretches"][0]["events"] + 2000
    same = lambda cs: [(c["id"], c["pattern"], c["apps"], c["count"]) for c in cs]  # noqa: E731
    assert same(workflow_candidates(heavy)) == same(workflow_candidates(light))
    state, _ = judge_request(heavy, workflow_candidates(heavy), [])
    assert all("keys" not in a for a in state["observed"]["apps"])
    assert all("events" not in s and "interactions" in s for s in state["observed"]["stretches"])
    assert sorted(a["typing_runs"] for a in state["observed"]["apps"]) == [1, 1] and "typing_runs" in state["context"]


def test_the_employee_summary_reaches_jev_as_context_bounded_and_never_as_a_candidate():
    observed = busy_session()
    candidates = workflow_candidates(observed)
    without, _ = judge_request(observed, candidates, [])
    assert "employee_summary" not in without and "employee_summary" not in without["context"]
    summary = "  Re-keying\n vendor   bills from PDF into QuickBooks " + "x" * 3000
    state, questions = judge_request(observed, candidates, [], summary=summary)
    assert state["employee_summary"].startswith("Re-keying vendor bills from PDF into QuickBooks x")
    assert len(state["employee_summary"]) == 2000 and "employee_summary" in state["context"]
    assert state["candidates"] == without["candidates"] and set(questions) == set(judge_request(observed, candidates, [])[1])
    assert "employee_summary" in questions["c1_workflow"]["instructions"] and "Re-keying" not in json.dumps(questions)
    seen = {}
    stub = lambda qs: {**answers("c1", 0.1, NONE, 0.9, 0.5, 0.9, 0.9), **answers("c2", 0.1, NONE, 0.9, 0.5, 0.9, 0.9)}  # noqa: E731
    captured = interpret_with_jev(
        observed,
        [],
        judge_fn=lambda state, questions: (seen.update(state), Judgment("stub-jev-v0", stub(questions), 1, 0, "stub"))[1],
        summary="Vendor bills",
    )
    assert seen["employee_summary"] == "Vendor bills" and captured.source == "stub"
    assert interpret(observed, [], summary="Vendor bills").event["candidates"] == 2
