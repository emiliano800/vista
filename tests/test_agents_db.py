"""Tier 2: synthetic discovery through the real queue, worker and tenant ledger.
Model is stubbed (conftest clears the API key). Skips without Postgres."""

import json
import os
import uuid
from decimal import Decimal

from tests.conftest import requires_db
from vista.jobs.worker import process_one

pytestmark = requires_db


def _drain(max_jobs: int = 20) -> None:
    for _ in range(max_jobs):
        if not process_one():
            return


def test_synthetic_companies_listed(client, tenant_factory):
    headers, _, _ = tenant_factory()
    companies = client.get("/synthetic/companies", headers=headers).json()
    assert {c["short"] for c in companies} == {"Meridian", "Harborline", "Castlebrook", "Northfield", "Keystone", "Ridgeway"}
    assert "11_billing_ar" in next(c for c in companies if c["short"] == "Ridgeway")["divisions"]


def test_synthetic_discovery_run_writes_ledger_and_findings(client, tenant_factory):
    headers, _, _ = tenant_factory()
    bad = client.post("/synthetic/discovery", json={"company": "nope", "division": "x"}, headers=headers)
    assert bad.status_code == 404

    run = client.post("/synthetic/discovery", json={"company": "ridgeway", "division": "11_billing_ar"}, headers=headers).json()
    assert run["status"] == "queued" and run["run_type"] == "synthetic_discovery"
    assert (run["company"], run["division"], run["sector"], run["agent_key"]) == (
        "Ridgeway",
        "11_billing_ar",
        "industrial_goods",
        "file_reviewer",
    )

    _drain()

    result = client.get(f"/runs/{run['id']}", headers=headers).json()
    assert result["status"] == "succeeded"
    assert result["started_at"] and result["finished_at"] >= result["started_at"] and result["error"] is None
    types = [e["event_type"] for e in result["events"]]
    assert types[0] == "step" and types[-1] == "result"
    assert types.count("tool_call") == types.count("model_call") >= 1
    model_calls = [e for e in result["events"] if e["event_type"] == "model_call"]
    assert all(e["data"]["source"] == "stub" for e in model_calls)

    findings = client.get("/findings", headers=headers).json()
    ours = [f for f in findings if f["run_id"] == run["id"]]
    assert ours and all(f["kind"] == "observed_fact" for f in ours)
    assert all(f["evidence"]["file"].startswith("11_billing_ar/") and f["evidence"]["company"] == "Ridgeway" for f in ours)
    assert all((f["company"], f["agent_key"]) == ("Ridgeway", "file_reviewer") for f in ours)
    assert any("mixed_date_formats" in f["title"] for f in ours)

    usage = client.get("/usage", headers=headers).json()
    assert usage["runs"] == 1 and usage["total_input_tokens"] >= 800 * len(model_calls)
    assert float(usage["total_cost_usd"]) > 0


def test_synthetic_findings_are_tenant_isolated(client, tenant_factory):
    h1, _, _ = tenant_factory()
    h2, _, _ = tenant_factory()
    client.post("/synthetic/discovery", json={"company": "castlebrook", "division": "07_claims"}, headers=h1)
    _drain()
    assert client.get("/findings", headers=h1).json()
    assert client.get("/findings", headers=h2).json() == []


def test_synthetic_analyze_run_writes_cross_company_findings(client, tenant_factory, tmp_path, monkeypatch):
    from vista.agents import analyze, synthetic
    from vista.agents.llm import Cassette, ChatResult
    from vista.config import settings

    headers, _, _ = tenant_factory()
    assert client.post("/synthetic/analyze", json={"sector": "retail"}, headers=headers).status_code == 404
    bad = client.post("/synthetic/analyze", json={"sector": "industrial_goods", "kinds": ["carrier_consolidation"]}, headers=headers)
    assert bad.status_code == 422

    # canned analyst answer for the software_overlap call, keyed by the exact prompt the worker will build
    by_company = {c: synthetic.tables_for(c) for c in synthetic.companies() if c.sector == "industrial_goods"}
    prompt = analyze.prepare("industrial_goods", by_company, "software_overlap")
    ref = "14_finance_gl/software_subscriptions.csv"
    answer = {
        "opportunities": [
            {
                "kind": "software_overlap",
                "title": "Zoom Workplace at two companies",
                "companies": ["Northfield", "Keystone"],
                "shared_key": "Zoom Workplace",
                "evidence": [f"Northfield:{ref}", f"Keystone:{ref}"],
                "detail": "Both pay list price separately.",
                "estimated_annual_value": 4800,
                "confidence": 0.8,
            },
            {"kind": "software_overlap", "title": "single company", "companies": ["Ridgeway"], "shared_key": "x", "confidence": 0.5},
        ],
        "rejected": ["Salesforce Sales Cloud vs Slack Business+: different products"],
    }
    Cassette(tmp_path / "c.json").put(
        prompt.key(settings.openai_model), ChatResult(model="canned", text=json.dumps(answer), input_tokens=900, output_tokens=120)
    )
    monkeypatch.setenv("VISTA_LLM_CASSETTE", str(tmp_path / "c.json"))

    run = client.post("/synthetic/analyze", json={"sector": "industrial_goods", "kinds": ["software_overlap"]}, headers=headers).json()
    assert run["status"] == "queued" and run["run_type"] == "synthetic_analyze"
    _drain()

    result = client.get(f"/runs/{run['id']}", headers=headers).json()
    assert result["status"] == "succeeded"
    events = result["events"]
    assert events[0]["data"]["companies"] == ["Keystone", "Northfield", "Ridgeway"]
    assert [e["event_type"] for e in events].count("model_call") == 1
    assert any(e["data"].get("message") == "look-alikes rejected" for e in events)
    assert events[-1]["data"] == {"findings_created": 1}

    ours = [f for f in client.get("/findings", headers=headers).json() if f["run_id"] == run["id"]]
    assert len(ours) == 1 and ours[0]["kind"] == "proposed_automation"
    assert (ours[0]["company"], ours[0]["agent_key"]) == ("Keystone, Northfield", "sector_merger")
    assert (result["sector"], result["agent_key"]) == ("industrial_goods", "sector_merger")
    assert ours[0]["title"] == "software_overlap: Zoom Workplace at two companies"
    assert ours[0]["evidence"]["companies"] == ["Keystone", "Northfield"]
    assert ours[0]["evidence"]["refs"] == [ref]

    usage = client.get("/usage", headers=headers).json()
    assert usage["runs"] == 1 and usage["total_input_tokens"] == 900


def test_synthetic_run_links_to_deal_of_same_name(client, tenant_factory):
    headers, _, _ = tenant_factory()
    deal = client.post("/deals", json={"name": "Ridgeway Fasteners & Supply"}, headers=headers).json()
    run = client.post("/synthetic/discovery", json={"company": "ridgeway", "division": "11_billing_ar"}, headers=headers).json()
    assert run["deal_id"] == deal["id"]
    assert [r["id"] for r in client.get(f"/runs?deal_id={deal['id']}", headers=headers).json()] == [run["id"]]
    _drain()
    assert client.get(f"/usage?deal_id={deal['id']}", headers=headers).json()["runs"] == 1
    assert client.get(f"/usage?deal_id={uuid.uuid4()}", headers=headers).json()["runs"] == 0


def test_run_finding_usage_filters_and_grouping(client, tenant_factory):
    headers, _, _ = tenant_factory()
    disc = client.post("/synthetic/discovery", json={"company": "ridgeway", "division": "11_billing_ar"}, headers=headers).json()
    ana = client.post("/synthetic/analyze", json={"sector": "industrial_goods", "kinds": ["software_overlap"]}, headers=headers).json()
    _drain()

    ids = lambda rows: {r["id"] for r in rows}  # noqa: E731
    assert ids(client.get("/runs?agent_key=file_reviewer", headers=headers).json()) == {disc["id"]}
    assert ids(client.get("/runs?run_type=synthetic_analyze&status=succeeded", headers=headers).json()) == {ana["id"]}
    assert ids(client.get("/runs?company=Ridgeway", headers=headers).json()) == {disc["id"]}
    assert client.get("/runs?since=2999-01-01T00:00:00Z", headers=headers).json() == []
    assert len(client.get("/runs?limit=1", headers=headers).json()) == 1

    findings = client.get(f"/findings?run_id={disc['id']}&kind=observed_fact", headers=headers).json()
    assert findings and all(f["company"] == "Ridgeway" for f in findings)
    assert client.get("/findings?agent_key=sector_merger&kind=observed_fact", headers=headers).json() == []
    assert client.get(f"/findings?company=Ridgeway&run_id={ana['id']}", headers=headers).json() == []

    usage = client.get("/usage?group_by=agent_key&group_by=company", headers=headers).json()
    assert usage["runs"] == 2
    keys = {(g["key"]["agent_key"], g["key"]["company"]) for g in usage["groups"]}
    assert keys == {("file_reviewer", "Ridgeway"), ("sector_merger", None)}
    assert sum(g["input_tokens"] for g in usage["groups"]) == usage["total_input_tokens"]
    assert client.get("/usage?group_by=colour", headers=headers).status_code == 422
    assert client.get("/usage?agent_key=sector_merger", headers=headers).json()["runs"] == 1

    fleet = client.get("/agents/analytics", headers=headers).json()
    assert fleet["runs_total"] == 2 and fleet["runs_month"] == 2
    by_key = {a["agent_key"]: a for a in fleet["agents"]}
    assert set(by_key) == {"recording_reviewer", "file_reviewer", "report_generator", "sector_merger", "computer_use"}
    assert by_key["file_reviewer"]["runs"] == 1 and by_key["file_reviewer"]["succeeded"] == 1
    assert by_key["file_reviewer"]["findings_total"] == len(client.get(f"/findings?run_id={disc['id']}", headers=headers).json())
    assert by_key["recording_reviewer"]["runs"] == 0 and by_key["recording_reviewer"]["last_run_at"] is None
    assert Decimal(fleet["total_cost_usd"]) == Decimal(usage["total_cost_usd"])
    assert len(fleet["by_day"]) == 30 and sum(d["runs"] for d in fleet["by_day"]) == 2
    assert {c["key"] for c in fleet["by_company"]} == {"Ridgeway", None}
    assert sum(k for k in fleet["findings_by_kind"].values()) == len(client.get("/findings", headers=headers).json())


def test_non_member_sees_only_portfolio_wide_runs(client, tenant_factory):
    from tests.test_permissions import _add_user

    headers, tenant_id, _ = tenant_factory()
    client.post("/deals", json={"name": "Ridgeway Fasteners & Supply"}, headers=headers)
    disc = client.post("/synthetic/discovery", json={"company": "ridgeway", "division": "11_billing_ar"}, headers=headers).json()
    ana = client.post("/synthetic/analyze", json={"sector": "industrial_goods", "kinds": ["software_overlap"]}, headers=headers).json()
    _drain()
    assert disc["deal_id"]

    outsider, _ = _add_user(tenant_id, "analyst@firm.example.com")
    assert {r["id"] for r in client.get("/runs", headers=outsider).json()} == {ana["id"]}
    assert client.get(f"/runs?deal_id={disc['deal_id']}", headers=outsider).status_code == 403
    assert client.get("/agents/analytics", headers=outsider).json()["runs_total"] == 1
    assert {f["run_id"] for f in client.get("/findings", headers=outsider).json()} <= {ana["id"]}
    assert client.get("/usage", headers=outsider).json()["runs"] == 1
    finding = client.get(f"/findings?run_id={disc['id']}", headers=headers).json()[0]
    assert client.patch(f"/findings/{finding['id']}", json={"status": "reviewed"}, headers=outsider).status_code == 403
    assert {r["id"] for r in client.get("/runs", headers=headers).json()} == {disc["id"], ana["id"]}


@requires_db
def test_agent_runs_need_owner_role_unless_admin(client, tenant_factory):
    from sqlalchemy import select

    from tests.test_permissions import _add_user, _tenant_schema
    from vista.db import tenant_session
    from vista.models.tenant import DealMembership

    headers, tenant_id, _ = tenant_factory()
    deal = client.post("/deals", json={"name": "Ridgeway Fasteners & Supply"}, headers=headers).json()
    outsider, outsider_id = _add_user(tenant_id, "analyst@firm.example.com")
    body = {"company": "ridgeway", "division": "11_billing_ar"}

    # No deal role at all: nothing may be started.
    assert client.post("/synthetic/discovery", json=body, headers=outsider).status_code == 403
    assert client.post("/synthetic/analyze", json={"sector": "industrial_goods"}, headers=outsider).status_code == 403
    assert client.post("/summaries", headers=outsider).status_code == 403

    with tenant_session(_tenant_schema(tenant_id)) as session:
        session.add(DealMembership(deal_id=uuid.UUID(deal["id"]), user_id=outsider_id, role="member"))
        session.commit()
    assert client.post("/synthetic/discovery", json=body, headers=outsider).status_code == 403

    with tenant_session(_tenant_schema(tenant_id)) as session:
        m = session.scalar(select(DealMembership).where(DealMembership.user_id == outsider_id))
        m.role = "owner"
        session.commit()
    run = client.post("/synthetic/discovery", json=body, headers=outsider).json()
    assert run["deal_id"] == deal["id"] and run["status"] == "queued"
    assert (
        client.post("/synthetic/analyze", json={"sector": "industrial_goods", "kinds": ["software_overlap"]}, headers=outsider).status_code
        == 201
    )
    assert client.post("/summaries", headers=outsider).status_code == 201
    # A company the caller does not own is still off limits once the firm has it as a deal.
    client.post("/deals", json={"name": "Keystone Bearing & Drive Co."}, headers=headers)
    assert (
        client.post("/synthetic/discovery", json={"company": "keystone", "division": "11_billing_ar"}, headers=outsider).status_code == 403
    )


@requires_db
def test_eval_runs_recorded_and_latest_per_scope(client, tenant_factory, tmp_path, monkeypatch):
    import subprocess
    import sys

    from tests.test_permissions import _add_user, _tenant_schema

    headers, tenant_id, _ = tenant_factory()
    body = {
        "phase": "analyze",
        "sector": "industrial_goods",
        "model": "gpt-4o-mini",
        "predictions": 8,
        "calls": 5,
        "cost_usd": "0.0021",
        "score": {"tp": 8, "fp": 0, "fn": 25, "trap_hits": 0, "matched": [["IND-01", "x"]], "missed": ["IND-02"]},
    }
    first = client.post("/evals", json=body, headers=headers)
    assert first.status_code == 201, first.text
    assert first.json()["agent_key"] == "sector_merger"
    assert first.json()["precision"] == 1.0 and round(first.json()["recall"], 3) == 0.242
    second = client.post("/evals", json={**body, "score": {**body["score"], "tp": 10, "fn": 23}}, headers=headers).json()
    client.post("/evals", json={"phase": "discover", "company": "Ridgeway", "score": {"tp": 1, "fp": 1, "fn": 0}}, headers=headers)
    assert client.post("/evals", json={"phase": "analyze", "score": {}}, headers=headers).status_code == 422
    assert client.post("/evals", json={"phase": "nope", "company": "x", "score": {}}, headers=headers).status_code == 422

    assert len(client.get("/evals", headers=headers).json()) == 3
    latest = client.get("/evals?latest=true", headers=headers).json()
    assert [(e["phase"], e["id"] == second["id"]) for e in latest] == [("discover", False), ("analyze", True)]
    assert [e["agent_key"] for e in client.get("/evals?phase=discover", headers=headers).json()] == ["file_reviewer"]

    quality = client.get("/agents/analytics", headers=headers).json()["quality"]
    assert {q["id"] for q in quality} == {e["id"] for e in latest}

    outsider, _ = _add_user(tenant_id, "analyst@firm.example.com")
    assert client.post("/evals", json=body, headers=outsider).status_code == 403
    assert len(client.get("/evals", headers=outsider).json()) == 3  # evals carry no deal data

    # The CLI records into eval_runs when --tenant is given (stub model, so ~0 score).
    out = subprocess.run(
        [
            sys.executable,
            "scripts/eval_agents.py",
            "--phase",
            "analyze",
            "--sector",
            "industrial_goods",
            "--kinds",
            "software_overlap",
            "--tenant",
            _tenant_schema(tenant_id),
        ],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "VISTA_OPENAI_API_KEY": ""},  # never spend tokens from the test suite
    )
    report = json.loads(out.stdout)
    assert report["eval_run_id"]
    rows = client.get("/evals?phase=analyze", headers=headers).json()
    assert rows[0]["id"] == report["eval_run_id"] and rows[0]["calls"] == 1 and rows[0]["model"] == "stub-model-v0"
