"""Tier 2: synthetic discovery through the real queue, worker and tenant ledger.
Model is stubbed (conftest clears the API key). Skips without Postgres."""

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

    _drain()

    result = client.get(f"/runs/{run['id']}", headers=headers).json()
    assert result["status"] == "succeeded"
    types = [e["event_type"] for e in result["events"]]
    assert types[0] == "step" and types[-1] == "result"
    assert types.count("tool_call") == types.count("model_call") >= 1
    model_calls = [e for e in result["events"] if e["event_type"] == "model_call"]
    assert all(e["data"]["source"] == "stub" for e in model_calls)

    findings = client.get("/findings", headers=headers).json()
    ours = [f for f in findings if f["run_id"] == run["id"]]
    assert ours and all(f["kind"] == "observed_fact" for f in ours)
    assert all(f["evidence"]["file"].startswith("11_billing_ar/") and f["evidence"]["company"] == "Ridgeway" for f in ours)
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
