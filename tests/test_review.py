from tests.conftest import requires_db
from tests.test_web import bundle, company, objects  # noqa: F401 — fixtures
from vista.jobs import handlers
from vista.jobs.worker import process_one
from vista.review import CONFIDENCE_THRESHOLD, parse_explanation


def drain():
    while process_one():
        pass


def test_parse_explanation_tolerates_prose_and_fences():
    parsed = parse_explanation('```json\n{"label": "Enter bills", "explanation": "x", "confidence": 1.7, "questions": ["Why?"]}\n```')
    assert parsed["label"] == "Enter bills" and parsed["confidence"] == 1.0 and parsed["questions"] == ["Why?"]
    prose = parse_explanation("Not sure.\n- What were you doing?\n- Who asked?")
    assert prose["confidence"] == 0 and prose["questions"] == ["What were you doing?", "Who asked?"]


@requires_db
def test_review_roundtrip_sections_worker_decisions(client, tenant_factory, bundle, objects, monkeypatch):  # noqa: F811
    headers, _, _ = tenant_factory()
    deal = company(client, headers)
    rid = client.post(f"/api/deals/{deal}/recordings", headers=headers, json=bundle).json()["id"]

    # Model is scripted: S1 confident, S2 unsure, session confident.
    answers = {
        "S1": ('{"label": "Enter vendor bills", "explanation": "Typing bills.", "confidence": 0.93, "unclear": [], "questions": []}', 0.93),
        "S2": (
            '{"label": "Read email", "explanation": "Unclear.", "confidence": 0.4, "unclear": ["why"], "questions": ["What for?"]}',
            0.4,
        ),
        "session": ('{"label": "Morning AP run", "explanation": "AP work.", "confidence": 0.9}', 0.9),
    }

    def fake_explain(prompt):
        key = prompt.split("\n")[0].split(":")[1].strip()
        return "test-model", parse_explanation(answers[key][0]), 10, 5

    monkeypatch.setattr(handlers, "explain_section", fake_explain)

    body = {
        "items": [
            {"id": "S1", "description": "SECTION: S1\nQuickBooks", "section": {"name": "QuickBooks", "app": "QuickBooks", "seconds": 600}},
            {"id": "S2", "description": "SECTION: S2\nOutlook", "section": {"name": "Outlook", "app": "Outlook", "seconds": 120}},
            {"id": "session", "description": "WHOLE: session\n...", "section": {"whole": True, "seconds": 720}},
        ]
    }
    review = client.put(f"/api/recordings/{rid}/review/sections", headers=headers, json=body).json()
    assert review["generating"] and review["summary"]["pending"] == 2 and all(i["status"] == "pending" for i in review["items"].values())
    assert review["run"]["status"] == "queued" and review["run"]["finished_at"] is None

    drain()
    review = client.get(f"/api/recordings/{rid}/review", headers=headers).json()
    assert not review["generating"] and review["threshold"] == CONFIDENCE_THRESHOLD
    assert review["run"]["status"] == "succeeded" and review["run"]["finished_at"] and review["run"]["error"] is None

    # The Recording Reviewer is accounted for like every other agent: one run, one model_call per section, usage.
    run = client.get(f"/api/runs/{review['run']['id']}", headers=headers).json()
    assert (run["run_type"], run["agent_key"], run["recording_id"], run["deal_id"]) == ("recording_review", "recording_reviewer", rid, deal)
    calls = [e for e in run["events"] if e["event_type"] == "model_call"]
    assert sorted(e["data"]["section"] for e in calls) == ["S1", "S2", "session"]
    assert run["events"][-1]["data"] == {"sections": 3, "failed": 0, "open": 2}
    usage = client.get("/api/usage?group_by=agent_key", headers=headers).json()
    assert usage["total_input_tokens"] == 30 and usage["groups"][0]["key"] == {"agent_key": "recording_reviewer"}
    assert review["items"]["S1"]["status"] == "proposed" and review["items"]["S2"]["status"] == "unsure"
    assert review["session"]["label"] == "Morning AP run"
    assert review["summary"] == {
        **review["summary"],
        "total": 2,
        "awaiting": 1,
        "unclear": 1,
        "open": 2,
        "resolved": 0,
        "unclear_ids": ["S2"],
        "awaiting_ids": ["S1"],
    }

    # Decisions follow the desktop rules: an unsure item cannot be approved.
    assert client.post(f"/api/recordings/{rid}/review/S2", headers=headers, json={"action": "approve"}).status_code == 409
    assert (
        client.post(f"/api/recordings/{rid}/review/S1", headers=headers, json={"action": "approve"}).json()["items"]["S1"]["final_label"]
        == "Enter vendor bills"
    )
    explained = client.post(
        f"/api/recordings/{rid}/review/S2",
        headers=headers,
        json={"action": "explain", "note": "Chasing a missing invoice", "answers": [{"q": "What for?", "a": "Vendor query"}]},
    ).json()["items"]["S2"]
    assert explained["status"] == "explained" and explained["final_note"] == "Chasing a missing invoice · What for? Vendor query"
    assert client.get(f"/api/recordings/{rid}/review", headers=headers).json()["summary"]["resolved"] == 2

    # Resubmitting never touches resolved items; a stranger cannot read or submit.
    review = client.put(f"/api/recordings/{rid}/review/sections", headers=headers, json=body).json()
    assert review["items"]["S1"]["status"] == "approved" and review["items"]["S2"]["status"] == "explained"
    other, _, _ = tenant_factory()
    assert client.get(f"/api/recordings/{rid}/review", headers=other).status_code == 404
    assert client.put(f"/api/recordings/{rid}/review/sections", headers=other, json=body).status_code == 404
