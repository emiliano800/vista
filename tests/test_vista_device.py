"""The device sidecar: one state function for recording and execution, leakage-checked output."""

from __future__ import annotations

import asyncio
import io
import json

import pytest

from taskmining.leakage import RecordingContext, check
from taskmining.normalise import Vocabulary
from vista_device.drivers import Result
from vista_device.drivers.browser import NodeView, candidates_from_views
from vista_device.drivers.desktop_macos import ElementView, candidates_from_elements
from vista_device.frame import Candidate, frame_from_candidates, l0_equal, path_shape, screen_class
from vista_device.server import Sidecar, serve
from vista_device.settle import settle

VOCAB = Vocabulary.build([["Save", "Customer name", "Amount", "Invoices"], ["Save", "Customer name", "Amount", "Search"]])


def _candidates(*, value: bool = False) -> list[Candidate]:
    return [
        Candidate("1", "textbox", "Customer name", "field", landmark="form", has_value=value),
        Candidate("2", "textbox", "Amount", "field", landmark="form"),
        Candidate("3", "button", "Save", "interactive", landmark="form"),
        Candidate("4", "link", "Invoices", "link", landmark="navigation"),
    ]


def test_frame_identity_is_l0_equality_not_values():
    a = frame_from_candidates(
        kind="browser",
        url="https://crm.example/invoices/1234/edit",
        title="Invoice 1234 — ACME",
        candidates=_candidates(),
        landmarks=["navigation", "form", "main"],
        vocab=VOCAB,
    )
    b = frame_from_candidates(
        kind="browser",
        url="https://crm.example/invoices/98765/edit",
        title="Invoice 98765 — Globex",
        candidates=_candidates(value=True),
        landmarks=["main", "form", "navigation"],
        vocab=VOCAB,
    )
    assert l0_equal(a.l0, b.l0)
    assert a.screen_class == b.screen_class
    assert a.fields_with_value == [] and b.fields_with_value == ["textbox|customer name|form"]
    assert a.l1["primary_button"]["name"] == "save"
    assert a.l1["landmarks"] == ["form", "main", "navigation"]
    assert a.l1["control_classes"] == ["button", "link", "textbox"]


def test_dialog_and_path_shape_split_nodes():
    base = frame_from_candidates(
        kind="browser", url="https://crm.example/invoices/1234", title="", candidates=_candidates(), landmarks=["main"], vocab=VOCAB
    )
    modal = frame_from_candidates(
        kind="browser",
        url="https://crm.example/invoices/1234",
        title="",
        candidates=_candidates(),
        landmarks=["main"],
        dialog="Discard changes?",
        vocab=VOCAB,
    )
    other = frame_from_candidates(
        kind="browser", url="https://crm.example/customers/1234", title="", candidates=_candidates(), landmarks=["main"], vocab=VOCAB
    )
    assert not l0_equal(base.l0, modal.l0) and modal.l1["modal"] is True
    assert any(m.startswith("ctx:") for m in modal.l0)
    assert not l0_equal(base.l0, other.l0)
    assert path_shape("https://crm.example/invoices/1234/edit?x=1") == "/invoices/{id}/edit"
    assert screen_class("browser", "/a", ["main", "form"]) == screen_class("browser", "/a", ["form", "main"])


def test_cloud_payload_has_no_raw_data_and_passes_leakage():
    f = frame_from_candidates(
        kind="browser",
        url="https://crm.example/invoices/1234",
        title="Invoice 1234 — ACME Corp",
        candidates=_candidates() + [Candidate("9", "row", "ACME Corp · €1,240.00 · 2024-03-01", "row")],
        landmarks=["main"],
        text="Customer ACME Corp owes €1,240.00",
        vocab=VOCAB,
    )
    cloud = f.cloud()
    dumped = json.dumps(cloud)
    for raw in ("ACME", "1240", "crm.example", "Invoice 1234", "owes"):
        assert raw not in dumped
    assert "url" not in cloud and "title" not in cloud and "text_excerpt" not in dumped
    ctx = RecordingContext.build(values=["ACME Corp", "1,240.00"], titles=["Invoice 1234 — ACME Corp"], vocab=VOCAB)
    assert check(cloud, ctx).ok, check(cloud, ctx).to_json()
    local = f.observation("obs-1")
    assert local["url"].startswith("https://crm.example") and local["text_excerpt"].startswith("Customer ACME")


def test_sensitive_frame_is_empty():
    f = frame_from_candidates(
        kind="browser",
        url="https://bank.example/login",
        title="Sign in",
        candidates=_candidates(),
        landmarks=["form"],
        sensitive=True,
        vocab=VOCAB,
    )
    assert f.sensitive and f.candidates == [] and f.url == "" and f.title == "" and f.text_local == ""
    assert f.cloud()["descriptors"] == []


def test_browser_views_to_candidates_bounded_and_typed():
    views = [
        NodeView(1, 11, "input", "textbox", "Customer name", {}, ("form", "main"), 100, 200, has_value=True),
        NodeView(2, 12, "button", "button", "Save", {"type": "submit"}, ("form",), 100, 400),
        NodeView(3, 13, "div", "dialog", "Discard changes?", {}, ()),
        NodeView(4, 14, "button", "button", "Cancel", {}, ("dialog",)),
        NodeView(5, 15, "span", "generic", "just text", {}, ()),
        NodeView(6, 16, "a", None, "Invoices", {"href": "/i"}, ("navigation",), 10, 10),
    ] + [NodeView(100 + i, 200 + i, "button", "button", f"Extra {i}", {}, ()) for i in range(60)]
    cands, landmarks, dialog = candidates_from_views(views, viewport=(1000, 800))
    assert len(cands) == 40
    assert dialog == "Discard changes?"
    assert set(landmarks) >= {"form", "main", "navigation", "dialog"}
    first = cands[0]
    assert first.kind == "field" and first.has_value and first.landmark == "form" and first.position != "unknown"
    assert cands[1].primary is True
    assert all(c.name != "just text" for c in cands)
    assert cands[0].to_json()["attrs"] == {"has_value": True} and cands[1].to_json()["attrs"] == {"primary": True}


def test_desktop_elements_to_candidates():
    views = [
        ElementView(0, "AXTextField", "Amount", ("AXGroup", "AXSheet", "AXWindow"), has_value=True),
        ElementView(1, "AXButton", "Save", ("AXToolbar", "AXWindow"), is_default=True),
        ElementView(2, "AXStaticText", "Hello", ()),
        ElementView(3, "AXButton", "Save", ("AXToolbar",)),
    ]
    cands, landmarks, dialog = candidates_from_elements(views, window=None)
    assert [c.role for c in cands] == ["textbox", "button"]
    assert cands[0].has_value and cands[0].landmark == "dialog" and cands[1].primary
    assert landmarks == ["dialog", "toolbar"] and dialog == ""


def test_settle_quiescence_window_and_cap():
    async def run(seq: list[str], *, window_ms: int = 200, cap_ms: int = 2000):
        t = [0.0]
        it = iter(seq)
        last = [seq[0]]

        async def fp():
            last[0] = next(it, last[0])
            return last[0]

        async def sleep(s):
            t[0] += s

        return await settle(fp, window_ms=window_ms, cap_ms=cap_ms, poll_ms=50, clock=lambda: t[0], sleep=sleep)

    assert asyncio.run(run(["a", "a", "a", "a", "a", "a"])) is True
    assert asyncio.run(run([str(i) for i in range(100)])) is False


class FakeDriver:
    kind = "browser"

    def __init__(self):
        self.steps: list[dict] = []
        self.closed = False

    def capabilities(self):
        return ["observe", "click"]

    async def observe(self):
        return frame_from_candidates(
            kind="browser",
            url="https://crm.example/invoices/7",
            title="Invoice 7 — ACME",
            candidates=_candidates(),
            landmarks=["main", "form"],
            vocab=VOCAB,
        )

    async def perform(self, step, frame):
        self.steps.append(step)
        if step.get("target_id") == "nope":
            return Result.refused("click", "stale_observation", "gone")
        return Result(True, "Clicked", frame=await self.observe(), result={"url_after": "https://crm.example/invoices/7"})

    async def close(self):
        self.closed = True


def _rpc(sidecar: Sidecar, requests: list[dict]) -> list[dict]:
    async def run() -> str:
        reader = asyncio.StreamReader()
        for r in requests:
            reader.feed_data((json.dumps(r) + "\n").encode())
        reader.feed_data(b"not json\n")
        reader.feed_eof()
        out = io.StringIO()
        await serve(sidecar, reader=reader, writer=out)
        return out.getvalue()

    return [json.loads(line) for line in asyncio.run(run()).splitlines()]


def test_sidecar_protocol_round_trip():
    fake = FakeDriver()
    sidecar = Sidecar(factories={"browser": lambda o: fake})
    responses = _rpc(
        sidecar,
        [
            {"id": 1, "method": "health"},
            {"id": 2, "method": "context", "params": {"values": ["ACME"], "titles": ["Invoice 7 — ACME"], "vocabulary": VOCAB.to_json()}},
            {"id": 3, "method": "observe", "params": {"kind": "browser"}},
            {"id": 4, "method": "open", "params": {"kind": "browser"}},
            {"id": 5, "method": "observe", "params": {"kind": "browser"}},
            {"id": 6, "method": "perform", "params": {"kind": "browser", "step": {"action": "click", "target_id": "3"}}},
            {"id": 7, "method": "perform", "params": {"kind": "browser", "step": {"action": "click", "target_id": "nope"}}},
            {
                "id": 8,
                "method": "frame",
                "params": {
                    "kind": "desktop",
                    "title": "Invoices — Numbers",
                    "candidates": [{"id": "a", "role": "button", "name": "Save", "kind": "interactive"}],
                    "landmarks": ["toolbar"],
                },
            },
            {"id": 9, "method": "open", "params": {"kind": "desktop"}},
            {"id": 10, "method": "nope"},
        ],
    )
    by_id = {r["id"]: r for r in responses}
    assert by_id[1]["result"]["drivers"] == {"browser": True, "desktop": False}
    assert by_id[3]["error"]["code"] == "harness_unsupported"
    assert by_id[4]["result"]["capabilities"] == ["observe", "click"]
    obs = by_id[5]["result"]
    assert obs["leakage"]["ok"] is True and obs["observation"]["url"] == "https://crm.example/invoices/7"
    assert "ACME" not in json.dumps(obs["cloud"])
    assert by_id[6]["result"]["ok"] is True and by_id[6]["result"]["observation"]["l0"] == obs["observation"]["l0"]
    assert by_id[7]["result"]["ok"] is False and by_id[7]["result"]["error"]["code"] == "stale_observation"
    assert by_id[8]["result"]["observation"]["l0"][0].startswith("in:") and by_id[8]["result"]["leakage"]["ok"]
    assert by_id[9]["error"]["code"] == "harness_unsupported"
    assert by_id[10]["error"]["code"] == "unknown_method"
    assert by_id[None]["error"]["code"] == "bad_request"
    assert fake.closed is True


@pytest.mark.parametrize("kind", ["browser", "desktop"])
def test_frame_method_matches_direct_construction(kind):
    sidecar = Sidecar()
    direct = frame_from_candidates(
        kind=kind, url="https://x.example/a/1", title="A 1", candidates=_candidates(), landmarks=["main"], vocab=VOCAB
    )
    via = asyncio.run(
        sidecar.frame(
            {
                "kind": kind,
                "url": "https://x.example/a/1",
                "title": "A 1",
                "candidates": [c.to_json() for c in _candidates()],
                "landmarks": ["main"],
                "vocabulary": VOCAB.to_json(),
            }
        )
    )
    assert via["observation"]["l0"] == direct.l0 and via["cloud"]["l1"] == direct.l1
