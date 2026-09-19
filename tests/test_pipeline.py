import io
from datetime import datetime, timedelta

from taskmining import abstraction, correlation, discovery, eventlog, preprocess
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


def test_unmatched_events_fall_back_to_other():
    steps = abstraction.abstract([("s", ev(0, EventType.CLICK, app="Notepad"))], [])
    assert steps[0].activity == "Other (Notepad)"


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


def test_xes_escapes_attributes():
    rules = [abstraction.ActivityRule('Act "quoted" & <x>', app="X")]
    steps = abstraction.abstract([("s", ev(0, EventType.CLICK, app="X", title='a "b" & <c>'))], rules)
    buf = io.StringIO()
    eventlog.to_xes(steps, buf)
    out = buf.getvalue()
    assert "&lt;x&gt;" in out and "&amp;" in out
