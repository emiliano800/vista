"""Metadata-only recorder analysis: deterministic facts, questions, and the
guard rails around the model's interpretation. No database, no model."""

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


# ---- Jev path: candidates from code, typed judgments, templated interpretation ----


def busy_session():
    events = [
        event(0, "Excel", "key", count=40),
        event(1, "Excel", "copy"),
        event(1, "Browser", "paste", second=20),
        event(2, "Browser", "click"),
        event(3, "Excel", "copy"),
        event(3, "Browser", "paste", second=30),
        event(4, "Excel", "click"),
        event(5, "Excel", "paste"),
        event(15, "Mail", "click"),
        event(16, "Mail", "key", count=12),
        event(17, "Excel", "click"),
        event(17, "Excel", "paste", second=30),
    ]
    return observe(events, MANIFEST)


def test_workflow_candidates_are_born_from_observed_facts_one_per_app_pair():
    candidates = workflow_candidates(busy_session())
    # Excel↔Browser shows up as a transfer, a loop and a stretch: one candidate, the transfer leading.
    assert [(c["id"], c["pattern"], c["apps"], c["count"]) for c in candidates] == [
        ("c1", "transfer", ["Excel", "Browser"], 2),
        ("c2", "loop", ["Excel", "Mail"], 2),
    ]
    c1 = candidates[0]
    assert c1["about"]["transfer"] == ["Excel", "Browser"] and c1["about"]["mean_latency_s"] == 25.0
    assert [list(a)[0] for a in c1["about"]["also"]] == ["loop", "stretch"] and c1["about"]["also"][1]["stretch"] == "s1"
    assert c1["evidence"] == (
        "Copied from Excel and pasted into Browser 2 times, 25.0s apart on average; "
        "also switched back and forth between Browser and Excel 4 times; "
        "also 4 switches among Excel, Browser between 09:00 and 09:05"
    )
    assert candidates[1]["evidence"] == "Switched back and forth between Excel and Mail 2 times" and "also" not in candidates[1]["about"]
    assert workflow_candidates(observe([event(0, "Mail")], MANIFEST)) == []


def test_judge_request_asks_four_typed_questions_per_candidate_over_the_facts():
    observed = busy_session()
    candidates = workflow_candidates(observed)
    state, questions = judge_request(observed, candidates, [{"filename": "ar.xlsx", "summary": "workbook", "excerpt": "Invoice,Total"}])
    assert [c["id"] for c in state["candidates"]] == ["c1", "c2"] and "about" not in state["candidates"][0]
    assert state["observed"]["transfers"] == observed["transfers"] and state["shared_documents"][0]["filename"] == "ar.xlsx"
    assert len(questions) == 4 * len(candidates)
    assert {questions[f"c1_{q}"]["type"] for q in ("workflow", "ask")} == {"noul"}
    assert questions["c1_kind"]["type"] == "choice" and NONE in questions["c1_kind"]["criteria"]
    assert questions["c1_mechanical"]["type"] == "score" and len(questions["c1_mechanical"]["criteria"]) == 4
    assert "Excel and Browser" in questions["c1_workflow"]["instructions"]


def answers(cid, workflow, kind, p_kind, mechanical, p_mech, ask):
    probs = {k: 0.0 for k in ("data_transfer", "lookup_and_enter", "reconciliation", "communication", "review_approval", NONE)}
    probs[kind] = p_kind
    return {
        f"{cid}_workflow": {"type": "noul", "noul": workflow},
        f"{cid}_kind": {"type": "choice", "choice": kind, "confidence": p_kind, "probabilities": probs},
        f"{cid}_mechanical": {"type": "score", "score": mechanical, "confidence": p_mech, "legend": {}, "probabilities": {}},
        f"{cid}_ask": {"type": "noul", "noul": ask},
    }


def judged():
    """c1 (Excel→Browser transfer): a mechanical data transfer, nothing to ask.
    c2 (Excel↔Mail loop): a communication loop, not mechanical, only the employee can say what it is."""
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
    assert interpretation["rejected"] == 0 and interpretation["judged"] == 2
    assert [w["name"] for w in interpretation["workflows"]] == ["Data transfer: Excel → Browser", "Communication loop: Excel ↔ Mail"]
    assert interpretation["workflows"][0]["confidence"] == 0.88  # min(p_workflow, p_kind)
    assert interpretation["workflows"][0]["about"]["transfer"] == ["Excel", "Browser"]
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
                "Around your work in Mail, you kept returning to Excel. "
                "What are you sending or receiving there, and who decides what happens next?"
            ),
            "about": {"apps": ["Excel", "Mail"], "candidate": "c2"},
        }
    ]
    assert interpretation["summary"] == "2 recurring workflows judged from 2 observed patterns; 1 looks mechanical enough to automate."
    # Declined either way: not a unit of work, or no kind fits.
    declined = Judgment(
        model="jev-1.13.0",
        answers={**answers("c1", 0.30, "data_transfer", 0.9, 2.9, 0.9, 0.9), **answers("c2", 0.80, NONE, 0.7, 2.9, 0.9, 0.9)},
    )
    interpretation, questions = apply_judgment(declined, candidates, model="jev-1.13.0", source="live")
    assert interpretation["workflows"] == [] and interpretation["rejected"] == 2 and questions == []
    assert interpretation["summary"] == "0 recurring workflows judged from 2 observed patterns."


def test_jev_stub_proposes_nothing_and_no_candidates_means_no_model_call(monkeypatch):
    monkeypatch.setattr(settings, "typesafe_api_key", None)
    monkeypatch.delenv("VISTA_JEV_CASSETTE", raising=False)
    result = interpret_with_jev(busy_session(), [])
    assert result.source == "stub" and result.model == "stub-jev-v0" and result.input_tokens > 0
    assert result.interpretation["workflows"] == [] and result.interpretation["rejected"] == 2 and result.questions == []
    assert result.event == {"interpreter": "jev", "candidates": 2, "questions": 8, "rejected": 2}
    quiet = interpret_with_jev(observe([event(0, "Mail")], MANIFEST), [], judge_fn=lambda *a: pytest.fail("must not call the model"))
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
    c = workflow_candidates(busy_session())[0]  # the Excel → Browser transfer
    docs = [{"filename": "ar_aging.xlsx", "summary": {"kind": "workbook"}, "excerpt": ""}]
    for kind in KIND_NAMES:
        definition = WorkflowDefinition.model_validate(draft_definition(kind, c, docs))  # raises on any schema violation
        assert definition.environment == "sandbox" and definition.limits.max_steps == 10
        assert "Excel" in definition.goal and "Browser" in definition.goal
        assert definition.required_inputs[:2] == ["Excel export or sample", "Browser field list"]
        assert "shared document: ar_aging.xlsx" in definition.required_inputs
        assert all(t.islower() and " " not in t for t in definition.allowed_tools)
    assert "2 times per session" in draft_definition("data_transfer", c, [])["goal"]


def test_instructions_are_specific_and_end_in_a_draft_or_a_smaller_fix():
    c = workflow_candidates(busy_session())[0]
    with_docs = instructions_for("data_transfer", c, [{"filename": "ar_aging.xlsx"}], "What is re-keyed?", True)
    assert with_docs[0].startswith(
        "Confirm with the employee what actually moves between Excel and Browser — the report asks: “What is re-keyed?”"
    )
    assert "ar_aging.xlsx" in with_docs[1] and "is the Excel source" in with_docs[1]
    assert "map each to a column in the Excel source" in with_docs[2]
    assert "copied from Excel and pasted into Browser 2 times" in with_docs[3]
    assert with_docs[4].startswith("Draft the workflow from the prefilled definition") and "$1.00 per run" in with_docs[4]
    assert "admin decision" in with_docs[5] and len(with_docs) == 6
    without = instructions_for("reconciliation", c, [], None, False)
    assert "the report asks" not in without[0] and "representative export or sample from Excel" in without[1]
    assert without[-1].startswith("Not mechanical enough to automate yet") and len(without) == 5


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
        ("inefficiency", "Communication loop: Excel ↔ Mail"),
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
