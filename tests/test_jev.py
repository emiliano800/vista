"""agents.jev: typed judgments with the same stub / cassette / live modes as agents.llm.
No network except the opt-in `live` test."""

import json

import httpx
import pytest

from vista.agents import jev
from vista.agents.jev import NONE, JudgeRequest, Judgment, choice, judge, noul, pick, score, stub_answers
from vista.agents.runtime import cost_usd, pricing
from vista.config import settings

QUESTIONS = {
    "recurring": noul("Is this a recurring unit of work?", {"true": "yes", "false": "no"}),
    "kind": pick("Which kind?", ["data_transfer", "reconciliation"]),
    "mechanical": score("How mechanical?", ["all judgment", "some repetition", "mostly mechanical", "fully mechanical"]),
}


@pytest.fixture()
def no_key(monkeypatch):
    monkeypatch.setattr(settings, "typesafe_api_key", None)
    monkeypatch.delenv("VISTA_JEV_CASSETTE", raising=False)


def test_question_builders_produce_the_api_shape():
    assert noul("q") == {"type": "noul", "instructions": "q"}
    assert choice("q", {"a": None, "b": "B"}) == {"type": "choice", "instructions": "q", "criteria": {"a": None, "b": "B"}}
    assert score("q", ("lo", "hi"))["criteria"] == ["lo", "hi"]
    p = pick("q", ["x", "y"])
    assert list(p["criteria"]) == ["x", "y", NONE] and p["criteria"][NONE]


def test_stub_answers_are_the_cautious_reviewer(no_key):
    j = judge({"anything": 1}, QUESTIONS)
    assert j.source == "stub" and j.model == jev.STUB_MODEL
    assert j.noul("recurring") == 0.0
    assert j.choice("kind") == (NONE, pytest.approx(1 / 3))
    assert j.score("mechanical") == (0.0, 0.0)
    assert set(j.probabilities("mechanical")) == {"0", "1", "2", "3"}
    assert cost_usd(j) == 0  # stub-jev is free; jev-* is priced on input only
    assert judge({}, {}).answers == {} and judge({}, {}).source == "stub"
    # Without a `none` escape the stub still picks something valid: the first label.
    assert stub_answers({"k": choice("q", {"first": None, "second": None})})["k"]["choice"] == "first"


def test_pricing_is_input_only_for_jev():
    in_price, out_price = pricing("jev-1.13.0")
    assert out_price == 0 and in_price > 0
    assert cost_usd(Judgment(model="jev-1.13.0", input_tokens=1_000_000, output_tokens=5000)) == in_price * 1_000_000
    # OpenRouter echoes provider-prefixed ids; the price is still Jev's, not the default.
    assert pricing("typesafe/jev-1.13") == pricing("~typesafe/jev-latest") == (in_price, out_price)
    assert pricing("openai/gpt-6-astra") == pricing("gpt-6-astra")


def test_request_key_is_stable_and_content_sensitive():
    a = JudgeRequest({"x": 1, "y": [2]}, QUESTIONS).key("jev-latest")
    assert a == JudgeRequest({"y": [2], "x": 1}, QUESTIONS).key("jev-latest")
    assert a != JudgeRequest({"x": 2, "y": [2]}, QUESTIONS).key("jev-latest")
    assert a != JudgeRequest({"x": 1, "y": [2]}, QUESTIONS).key("jev-1.13.0")


def test_cassette_replays_and_stub_does_not_record(no_key, tmp_path):
    path = tmp_path / "jev.json"
    key = JudgeRequest({"s": 1}, {"q": noul("?")}).key(settings.typesafe_model)
    path.write_text(
        json.dumps({key: {"model": "jev-1.13.0", "answers": {"q": {"type": "noul", "noul": 0.83}}, "input_tokens": 41, "output_tokens": 0}})
    )
    cassette = jev.Cassette(path)
    hit = judge({"s": 1}, {"q": noul("?")}, cassette=cassette)
    assert hit.source == "cassette" and hit.noul("q") == 0.83 and hit.input_tokens == 41
    miss = judge({"s": 2}, {"q": noul("?")}, cassette=cassette)
    assert miss.source == "stub" and cassette.misses == 1
    assert set(json.loads(path.read_text())) == {key}  # a stub answer is never recorded as evidence


class FakePost:
    """Scripted httpx.post: a list of (status, json) responses consumed in order."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, url, *, json, headers, timeout):
        self.calls.append({"url": url, "json": json, "headers": headers})
        status, body = self.responses.pop(0)
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))


ANSWERS = {
    "recurring": {"type": "noul", "noul": 0.91},
    "kind": {
        "type": "choice",
        "choice": "data_transfer",
        "confidence": 0.88,
        "probabilities": {"data_transfer": 0.9, "reconciliation": 0.07, NONE: 0.03},
    },
    "mechanical": {"type": "score", "score": 2.6, "confidence": 0.7, "legend": {}, "probabilities": {"0": 0, "1": 0.1, "2": 0.2, "3": 0.7}},
}


def test_live_call_sends_the_contract_and_reads_typed_answers(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "typesafe_api_key", "k-test")
    monkeypatch.setattr(settings, "typesafe_model", "jev-latest")
    fake = FakePost(
        (429, {"error": "slow down"}),
        (200, {"model": "jev-1.13.0", "answers": ANSWERS, "usage": {"input_tokens": 812, "output_tokens": 0}}),
    )
    monkeypatch.setattr(jev.httpx, "post", fake)
    monkeypatch.setattr(jev.time, "sleep", lambda s: None)
    cassette = jev.Cassette(tmp_path / "c.json")
    j = judge({"doc": "x"}, QUESTIONS, cassette=cassette)
    assert len(fake.calls) == 2  # retried once after 429
    sent = fake.calls[-1]
    assert sent["url"] == "https://api.typesafe.ai/v1/systemone"
    assert sent["headers"]["Authorization"] == "Bearer k-test"
    assert sent["json"] == {"model": "jev-latest", "state": {"doc": "x"}, "questions": QUESTIONS}
    assert j.source == "live" and j.model == "jev-1.13.0" and j.input_tokens == 812
    assert j.noul("recurring") == 0.91
    assert j.choice("kind") == ("data_transfer", 0.9)
    assert j.score("mechanical") == (2.6, 0.7)
    assert cost_usd(j) == pricing("jev")[0] * 812
    # Recorded, and replayed without another call.
    assert judge({"doc": "x"}, QUESTIONS, cassette=cassette).source == "cassette" and len(fake.calls) == 2


def test_live_errors_are_loud(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "typesafe_api_key", "k-test")
    cassette = jev.Cassette(tmp_path / "c.json")
    monkeypatch.setattr(jev.httpx, "post", FakePost((401, {"detail": "bad key"})))
    with pytest.raises(RuntimeError, match="Jev 401"):
        judge({"doc": "x"}, QUESTIONS, cassette=cassette)
    monkeypatch.setattr(
        jev.httpx, "post", FakePost((200, {"model": "jev-1.13.0", "answers": {"recurring": ANSWERS["recurring"]}, "usage": {}}))
    )
    with pytest.raises(RuntimeError, match="answered without"):
        judge({"doc": "x"}, QUESTIONS, cassette=cassette)
    assert not (tmp_path / "c.json").exists()  # a failed call is never recorded


@pytest.mark.live
def test_live_jev_selects_among_candidates():
    if not settings.typesafe_api_key:
        pytest.skip("VISTA_TYPESAFE_API_KEY not set")
    j = judge(
        {"command": "copy the invoice total from the workbook into the portal", "candidates": ["Excel", "Browser", "Mail"]},
        {"target": pick("Which candidate is the destination of the paste?", ["Excel", "Browser", "Mail"])},
    )
    assert j.source == "live" and "jev" in j.model  # "jev-1.13" direct, "typesafe/jev-1.13" via OpenRouter
    assert j.choice("target")[0] == "Browser" and j.choice("target")[1] > 0.5
    assert j.input_tokens > 0 and cost_usd(j) > 0
