"""Tier 2: synthetic discovery through the real queue, worker and tenant ledger.
Model is stubbed (conftest clears the API key). Skips without Postgres."""

import json

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
    assert {f["run_id"] for f in client.get("/findings", headers=outsider).json()} <= {ana["id"]}
    assert client.get("/usage", headers=outsider).json()["runs"] == 1
    finding = client.get(f"/findings?run_id={disc['id']}", headers=headers).json()[0]
    assert client.patch(f"/findings/{finding['id']}", json={"status": "reviewed"}, headers=outsider).status_code == 403
    assert {r["id"] for r in client.get("/runs", headers=headers).json()} == {disc["id"], ana["id"]}
