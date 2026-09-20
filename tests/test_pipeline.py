import io
from datetime import datetime, timedelta

from taskmining import abstraction, analytics, correlation, discovery, eventlog, preprocess
from taskmining.capture import JsonlSource, SyntheticSource
from taskmining.models import EventType, RawEvent, write_jsonl
from taskmining.pipeline import Pipeline

T0 = datetime(2026, 1, 1, 9, 0, 0)


def ev(offset_s, typ, app="App", title="Win", text="", user="u", **kw):
    return RawEvent(T0 + timedelta(seconds=offset_s), user, typ, app, title, text=text, **kw)


def test_redact_text_masks_pii():
    s = preprocess.redact_text("mail a.b@x.io tel +49 30 1234567 iban DE89370400440532013000")
    assert "a.b@x.io" not in s and "<EMAIL>" in s
    assert "1234567" not in s and "<PHONE>" in s
    assert "<IBAN>" in s


def test_aggregate_keystrokes_merges_bursts_and_redacts():
    events = [ev(i * 0.1, EventType.KEY, text=c) for i, c in enumerate("me@x.io")]
    events.append(ev(5, EventType.CLICK))
    events += [ev(10 + i * 0.1, EventType.KEY, text=c) for i, c in enumerate("abc")]
    out = preprocess.aggregate_keystrokes(events)
    assert [e.event_type for e in out] == [EventType.KEY, EventType.CLICK, EventType.KEY]
    assert out[0].payload["n_keys"] == 7 and out[0].text == "<EMAIL>"
    assert out[2].payload["n_keys"] == 3 and out[2].text == "abc"


def test_sessionize_splits_on_idle_gap():
    events = [ev(0, EventType.CLICK), ev(60, EventType.CLICK), ev(60 + 11 * 60, EventType.CLICK)]
    sids = [sid for sid, _ in preprocess.sessionize(events, idle=timedelta(minutes=10))]
    assert sids == ["u#1", "u#1", "u#2"]


def test_abstract_merges_consecutive_same_activity():
    rules = [abstraction.ActivityRule("A", app="X"), abstraction.ActivityRule("B", app="Y")]
    events = [
        ev(0, EventType.CLICK, app="X"),
        ev(1, EventType.COPY, app="X"),
        ev(2, EventType.PASTE, app="Y"),
        ev(3, EventType.CLICK, app="X"),
    ]
    steps = abstraction.abstract([("s", e) for e in events], rules)
    assert [s.activity for s in steps] == ["A", "B", "A"]
    assert steps[0].n_events == 2 and steps[0].n_copies == 1 and steps[1].n_pastes == 1
    assert steps[0].duration_s == 1.0


def test_unmatched_events_fall_back_to_app_name():
    steps = abstraction.abstract([("s", ev(0, EventType.CLICK, app="Notepad"))], [])
    assert steps[0].activity == "Notepad"


def test_correlate_forward_and_backward_fill():
    rules = [
        abstraction.ActivityRule("Mail", app="Mail"),
        abstraction.ActivityRule("Pdf", app="Pdf"),
        abstraction.ActivityRule("Erp", app="Erp"),
    ]
    events = [
        ev(0, EventType.FOCUS, app="Mail", title="Inbox"),
        ev(5, EventType.FOCUS, app="Pdf", title="INV-12345.pdf"),
        ev(10, EventType.FOCUS, app="Erp", title="Enter Invoice"),
        ev(10 * 60, EventType.FOCUS, app="Mail", title="Inbox"),
    ]
    steps = abstraction.abstract([("s", e) for e in events], rules)
    correlation.correlate(steps)
    assert [s.case_id for s in steps] == ["INV-12345", "INV-12345", "INV-12345", ""]


def test_discover_builds_dfg_and_variants():
    rules = [abstraction.ActivityRule("A", app="A"), abstraction.ActivityRule("B", app="B")]
    events = []
    for i, inv in enumerate(["INV-1000", "INV-1001", "INV-1002"]):
        base = i * 100
        events.append(ev(base, EventType.CLICK, app="A", title=inv))
        events.append(ev(base + 5, EventType.CLICK, app="B", title=inv))
        if i == 2:
            events.append(ev(base + 10, EventType.CLICK, app="A", title=inv))
    steps = abstraction.abstract([("s", e) for e in events], rules)
    correlation.correlate(steps)
    res = discovery.discover(steps)
    assert len(res.cases) == 3
    assert res.variants[0].activities == ("A", "B") and res.variants[0].count == 2
    assert res.variants[1].activities == ("A", "B", "A")
    ab = next(e for e in res.edges if (e.src, e.dst) == ("A", "B"))
    assert ab.count == 3 and ab.mean_wait_s == 5.0
    assert res.rework["A"] == 1
    assert '"A" -> "B"' in res.to_dot()
    assert "flowchart LR" in res.to_mermaid()


def test_end_to_end_synthetic_and_exports(tmp_path):
    res = Pipeline().run(SyntheticSource(n_cases=12, seed=1))
    assert len(res.discovery.cases) == 12
    assert all(s.case_id for s in res.steps), "every step should be correlated"
    assert all(u.startswith("user_") for u in {s.user for s in res.steps})
    assert not any("@" in e.text for e in res.clean)
    assert res.automation and res.automation[0].score >= res.automation[-1].score
    res.write(tmp_path)
    assert (tmp_path / "event_log.xes").exists() and (tmp_path / "summary.json").exists()
    xes = (tmp_path / "event_log.xes").read_text()
    assert xes.count("<trace>") == 12
    csv_text = (tmp_path / "event_log.csv").read_text().splitlines()
    assert csv_text[0].startswith("case_id,activity") and len(csv_text) == len(res.steps) + 1


def test_jsonl_roundtrip():
    events = list(SyntheticSource(n_cases=2, seed=3).events())
    buf = io.StringIO()
    write_jsonl(events, buf)
    buf.seek(0)
    back = list(JsonlSource(buf).events())
    assert back == events


def test_recorder_events_flow_through_preprocess():
    # shape written by src/recorder/ (Electron): shortcuts and screenshots must survive
    # keystroke aggregation without being merged into typing bursts
    events = [
        ev(0, EventType.FOCUS, app="Excel", title="AP tracker.xlsx"),
        ev(0.5, EventType.SCREEN, app="Excel", title="AP tracker.xlsx", payload={"image": "shots/000001.jpg"}),
        ev(1, EventType.KEY, app="Excel", title="AP tracker.xlsx", text="4"),
        ev(1.1, EventType.KEY, app="Excel", title="AP tracker.xlsx", text="2"),
        ev(2, EventType.SHORTCUT, app="Excel", title="AP tracker.xlsx", text="Ctrl+S", payload={"modifiers": ["ctrl"]}),
        ev(3, EventType.KEY, app="Excel", title="AP tracker.xlsx", text="x"),
    ]
    line = events[4].to_json()
    assert RawEvent.from_json(line) == events[4]
    out = preprocess.aggregate_keystrokes(events)
    assert [e.event_type for e in out] == [EventType.FOCUS, EventType.SCREEN, EventType.KEY, EventType.SHORTCUT, EventType.KEY]
    assert out[2].payload["n_keys"] == 2 and out[3].text == "Ctrl+S"


def test_xes_escapes_attributes():
    rules = [abstraction.ActivityRule('Act "quoted" & <x>', app="X")]
    steps = abstraction.abstract([("s", ev(0, EventType.CLICK, app="X", title='a "b" & <c>'))], rules)
    buf = io.StringIO()
    eventlog.to_xes(steps, buf)
    out = buf.getvalue()
    assert "&lt;x&gt;" in out and "&amp;" in out


# --- annotations & provenance -------------------------------------------------

from taskmining import annotations  # noqa: E402
from taskmining.models import OFF_SCREEN_APP, Annotation, Source, Step  # noqa: E402


def step(offset_s, dur_s, activity, app="App", title="Win", user="u", sid="u#1", **kw):
    return Step(
        activity=activity,
        start=T0 + timedelta(seconds=offset_s),
        end=T0 + timedelta(seconds=offset_s + dur_s),
        user=user,
        session_id=sid,
        app=app,
        window_title=title,
        **{"n_events": 10, "n_keys": 10, **kw},
    )


def ann(offset_s, dur_s, label, **kw):
    return Annotation("u", T0 + timedelta(seconds=offset_s), T0 + timedelta(seconds=offset_s + dur_s), label, **kw)


def test_annotation_jsonl_and_csv_round_trip():
    a = ann(0, 3600, "Phone: chase invoices", note="called 3 vendors", case_id="INV-1", author="analyst")
    buf = io.StringIO()
    annotations.write_annotations([a], buf)
    buf.seek(0)
    assert annotations.read_annotations(buf) == [a]
    csv_text = "user,start,end,label\nu,2026-01-01T09:00:00,2026-01-01T10:00:00,Paper filing\n"
    [b] = annotations.read_annotations(io.StringIO(csv_text), fmt="csv")
    assert b.label == "Paper filing" and b.author == "employee" and b.end - b.start == timedelta(hours=1)


def test_apply_annotations_relabels_and_splits_steps():
    steps = [step(0, 100, "Other (Excel)", app="Excel", n_keys=100)]
    out = annotations.apply_annotations(steps, [ann(40, 30, "Reconcile tracker", note="fix me@x.io")])
    assert [(s.activity, s.activity_source) for s in out] == [
        ("Other (Excel)", Source.RULE),
        ("Reconcile tracker", Source.HUMAN),
        ("Other (Excel)", Source.RULE),
    ]
    assert [s.duration_s for s in out] == [40, 30, 30]
    assert out[1].n_keys == 30 and out[1].note == "fix <EMAIL>"


def test_session_summary_annotation_does_not_relabel_steps():
    steps = [step(0, 100, "Enter Invoice"), step(100, 100, "Other (Excel)", app="Excel")]
    whole = ann(0, 200, "Month-end AP run", scope="session")
    section = ann(100, 100, "Reconcile tracker", scope="section")
    out = annotations.apply_annotations(steps, [whole, section])
    assert [s.activity for s in out] == ["Enter Invoice", "Reconcile tracker"]
    assert all(s.app != OFF_SCREEN_APP for s in out)
    [rt] = annotations.read_annotations(io.StringIO(whole.to_json() + "\n"))
    assert rt.scope == "session"


def test_apply_annotations_creates_off_screen_steps_and_human_case():
    steps = [step(0, 60, "Send Email", app="Outlook"), step(3600, 60, "Read Email", app="Outlook")]
    a = ann(120, 3000, "Phone: chase overdue invoices", case_id="INV-9")
    out = annotations.apply_annotations(steps, [a])
    off = [s for s in out if s.app == OFF_SCREEN_APP]
    assert len(off) == 1
    assert off[0].activity == a.label and off[0].session_id == "u#1"
    assert off[0].case_id == "INV-9" and off[0].case_source == Source.HUMAN
    assert off[0].activity_source == Source.HUMAN and off[0].n_events == 0


def test_apply_annotations_skips_tiny_off_screen_fragments():
    steps = [step(0, 60, "A")]
    out = annotations.apply_annotations(steps, [ann(50, 20, "X")])  # only 10s uncovered
    assert all(s.app != OFF_SCREEN_APP for s in out)


def test_correlate_records_provenance_and_respects_human_case():
    steps = [
        step(0, 10, "Open", title="Inbox"),
        step(10, 10, "Read", title="Invoice INV-100201"),
        step(20, 10, "Post", title="SAP"),
        step(30, 10, "Meeting", app=OFF_SCREEN_APP),
        step(40, 10, "Note", case_id="INV-7", case_source=Source.HUMAN),
    ]
    correlation.correlate(steps)
    assert [s.case_source for s in steps] == [Source.FILLED, Source.OBSERVED, Source.FILLED, None, Source.HUMAN]
    assert steps[0].case_id == steps[2].case_id == "INV-100201"
    assert steps[3].case_id == "" and steps[4].case_id == "INV-7"


def test_correlate_episode_fallback_groups_bursts():
    steps = [step(0, 10, "A"), step(20, 10, "B"), step(1000, 10, "C"), step(1020, 10, "D", user="v", sid="v#1")]
    correlation.correlate(steps, episodes=True)
    assert [s.case_id for s in steps] == ["EP-u-1", "EP-u-1", "EP-u-2", "EP-v-1"]
    assert all(s.case_source == Source.EPISODE for s in steps)
    steps2 = [step(0, 10, "A")]
    correlation.correlate(steps2)
    assert steps2[0].case_id == "" and steps2[0].case_source is None


def test_open_questions_for_gaps_and_fallback_labels():
    steps = [step(0, 60, "Other (Excel)", activity_source=Source.FALLBACK), step(3600, 60, "Send Email")]
    qs = annotations.open_questions(steps)
    kinds = sorted(q.kind for q in qs)
    assert kinds == ["gap", "unlabelled"]
    gap = next(q for q in qs if q.kind == "gap")
    assert gap.start == steps[0].end and gap.end == steps[1].start and gap.user == "u"


def test_eventlog_exports_provenance_and_note():
    s = step(0, 10, "Phone", app=OFF_SCREEN_APP, case_id="INV-1", case_source=Source.HUMAN, note='say "hi"')
    s.activity_source = Source.HUMAN
    buf = io.StringIO()
    eventlog.to_csv([s], buf)
    header, row = buf.getvalue().strip().splitlines()
    assert header.endswith("activity_source,case_source,note") and row.endswith('human,human,"say ""hi"""')
    buf = io.StringIO()
    eventlog.to_xes([s], buf)
    xes = buf.getvalue()
    assert '<string key="vista:activity_source" value="human"/>' in xes
    assert '<string key="vista:case_source" value="human"/>' in xes
    assert '<string key="vista:note" value=\'say "hi"\'/>' in xes


def test_linked_pastes_become_transfers_and_data_flows(tmp_path):
    # shape written by src/recorder/: a paste whose clipboard hash matched an earlier copy
    # carries the source app; only cross-app links count as transfers
    linked = {"clip_hash": "ab12", "source_app": "Acrobat", "source_title": "INV-1.pdf", "transfer_ms": 4200, "chars": 9}
    events = [
        ev(0, EventType.FOCUS, app="Acrobat", title="INV-1.pdf"),
        ev(1, EventType.COPY, app="Acrobat", title="INV-1.pdf", text="INV-1", payload={"clip_hash": "ab12", "chars": 9}),
        ev(5, EventType.FOCUS, app="QuickBooks", title="Enter Bills"),
        ev(6, EventType.PASTE, app="QuickBooks", title="Enter Bills", text="INV-1", payload=linked),
        ev(7, EventType.PASTE, app="QuickBooks", title="Enter Bills", text="INV-1", payload={**linked, "transfer_ms": 5800}),
        ev(8, EventType.PASTE, app="QuickBooks", title="Enter Bills", text="x", payload={"clip_hash": "zz", "chars": 1}),
        ev(9, EventType.PASTE, app="QuickBooks", title="Enter Bills", text="y", payload={"clip_hash": "q", "source_app": "QuickBooks"}),
    ]
    steps = abstraction.abstract(preprocess.sessionize(events))
    qb = [s for s in steps if s.app == "QuickBooks"][0]
    assert (qb.n_pastes, qb.n_transfers) == (4, 2)
    assert [s.n_transfers for s in steps if s.app == "Acrobat"] == [0]

    flows = analytics.data_flows(events)
    assert len(flows) == 1
    assert (flows[0].source_app, flows[0].target_app, flows[0].count, flows[0].chars) == ("Acrobat", "QuickBooks", 2, 18)
    assert flows[0].mean_transfer_s == 5.0

    buf = io.StringIO()
    write_jsonl(events, buf)
    buf.seek(0)
    res = Pipeline(pseudonymize=False).run(JsonlSource(buf))
    assert [f.to_dict()["from"] for f in res.data_flows] == ["Acrobat"]
    res.write(tmp_path / "out")
    csv_text = (tmp_path / "out" / "event_log.csv").read_text()
    assert "n_transfers" in csv_text.splitlines()[0]
    assert '"data_flows"' in (tmp_path / "out" / "summary.json").read_text()
    assert 'key="vista:n_transfers" value="2"' in (tmp_path / "out" / "event_log.xes").read_text()


def test_pipeline_with_annotations_pseudonymises_users_and_writes_artifacts(tmp_path):
    src = SyntheticSource(n_cases=40, seed=7)
    human = src.annotations()
    assert human and all(a.user in {"alice", "bob", "carol"} for a in human)
    res = Pipeline().run(src, human)
    assert len(res.annotations) == len(human)
    assert all(a.user.startswith("user_") for a in res.annotations)
    human_steps = [s for s in res.steps if s.activity_source == Source.HUMAN]
    assert human_steps, "annotations must relabel or add steps"
    assert {s.activity for s in human_steps} <= {a.label for a in human}
    off = [s for s in human_steps if s.app == OFF_SCREEN_APP]
    assert off and all(s.case_source == Source.EPISODE for s in off)
    assert not [s for s in res.steps if not s.case_id]
    res.write(tmp_path)
    assert (tmp_path / "annotations.jsonl").exists() and (tmp_path / "questions.json").exists()
    summary = (tmp_path / "summary.json").read_text()
    assert '"provenance"' in summary and '"off_screen_hours"' in summary


def test_cli_run_with_annotations_file(tmp_path, capsys):
    from taskmining.__main__ import main

    raw, annf = tmp_path / "raw.jsonl", tmp_path / "ann.jsonl"
    assert main(["generate", "--cases", "5", "--out", str(raw), "--annotations-out", str(annf)]) == 0
    assert main(["run", "--input", str(raw), "--annotations", str(annf), "--out", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "annotations:" in out and "'human':" in out
