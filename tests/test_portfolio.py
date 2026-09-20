"""The PE analyst portfolio: the firm is the tenant, companies are deals."""

import base64

import pytest

from tests.conftest import requires_db


def file(name, rows):
    body = "\n".join(",".join(str(c) for c in row) for row in rows) + "\n"
    return {"name": name, "content": base64.b64encode(body.encode()).decode()}


PURCHASES_CHEAP = file(
    "purchases.csv",
    [["part_number", "qty", "unit_cost", "supplier_name"], ["BRG-204", "100", "12.50", "Acme"]],
)
PURCHASES_DEAR = file(
    "purchases.csv",
    [["part_number", "qty", "unit_cost", "supplier_name"], ["BRG-204", "80", "15.00", "Globex"]],
)
SOFTWARE = file(
    "software.csv",
    [["product", "monthly_cost", "seats", "active_seats"], ["Microsoft 365", "336.00", "28", "20"]],
)


@pytest.fixture()
def import_store(monkeypatch):
    class Store:
        def __init__(self):
            self.items = {}

        def put_object(self, **kwargs):
            self.items[kwargs["Key"]] = kwargs["Body"]

    store = Store()
    monkeypatch.setattr("vista.api.imports.s3_client", lambda: store)
    return store


def commit_import(client, headers, deal_id, files, as_of="2026-09-20"):
    batch = client.post(f"/api/deals/{deal_id}/imports", headers=headers, json={"files": files, "as_of": as_of}).json()
    choices = {"tables": [{k: t[k] for k in ["id", "kind", "mapping"]} for t in batch["tables"]]}
    committed = client.post(f"/api/imports/{batch['id']}/commit", headers=headers, json=choices)
    assert committed.status_code == 200, committed.text
    return committed.json()


@requires_db
def test_companies_report_zero_until_an_import_is_committed(client, tenant_factory, import_store):
    headers, _, _ = tenant_factory()
    deal = client.post("/api/deals", headers=headers, json={"name": "Harbor Heating"}).json()["id"]

    rows = client.get("/api/portfolio/companies", headers=headers).json()
    assert [r["name"] for r in rows] == ["Harbor Heating"]
    assert rows[0]["records"] == 0 and rows[0]["as_of"] is None

    detail = client.get(f"/api/portfolio/companies/{deal}", headers=headers).json()
    assert detail["subscriptions"] == [] and "subscriptions" in detail["unsourced"]

    commit_import(client, headers, deal, [SOFTWARE])
    detail = client.get(f"/api/portfolio/companies/{deal}", headers=headers).json()
    assert len(detail["subscriptions"]) == 1
    assert detail["subscriptions"][0]["product"] == "Microsoft 365"
    assert "subscriptions" not in detail["unsourced"], "a sourced bucket is no longer unsourced"
    assert "purchases" in detail["unsourced"], "nothing was imported for purchases"
    assert client.get("/api/portfolio/companies", headers=headers).json()[0]["as_of"] == "2026-09-20"


@requires_db
def test_tasks_are_scoped_to_a_company_and_log_activity(client, tenant_factory):
    headers, _, _ = tenant_factory()
    deal = client.post("/api/deals", headers=headers, json={"name": "Summit Mechanical"}).json()["id"]

    created = client.post(
        "/api/portfolio/tasks",
        headers=headers,
        json={"deal_id": deal, "title": "Collect on overdue invoices", "priority": "High"},
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["status"] == "Open" and task["completed_at"] is None

    assert (
        client.post("/api/portfolio/tasks", headers=headers, json={"deal_id": deal, "title": "x", "priority": "Urgent"}).status_code == 422
    )
    missing = "00000000-0000-4000-8000-000000000000"
    assert client.post("/api/portfolio/tasks", headers=headers, json={"deal_id": missing, "title": "x"}).status_code == 404

    done = client.patch(f"/api/portfolio/tasks/{task['id']}", headers=headers, json={"status": "Complete"}).json()
    assert done["completed_at"] is not None
    reopened = client.patch(f"/api/portfolio/tasks/{task['id']}", headers=headers, json={"status": "Open"}).json()
    assert reopened["completed_at"] is None, "reopening clears the completion stamp"

    summaries = [a["summary"] for a in client.get("/api/portfolio/activity", headers=headers).json()]
    assert any("Task created" in s for s in summaries)
    assert any("Task complete" in s for s in summaries)


@requires_db
def test_analysis_compares_the_same_sku_across_companies(client, tenant_factory, import_store):
    headers, _, _ = tenant_factory()
    cheap = client.post("/api/deals", headers=headers, json={"name": "Keystone Bearing"}).json()["id"]
    dear = client.post("/api/deals", headers=headers, json={"name": "Ridgeway Fasteners"}).json()["id"]
    commit_import(client, headers, cheap, [PURCHASES_CHEAP])
    commit_import(client, headers, dear, [PURCHASES_DEAR])

    run = client.post("/api/portfolio/analysis", headers=headers)
    assert run.status_code == 201, run.text
    body = run.json()
    assert body["companies"] == 2 and body["skus_compared"] == 1
    assert len(body["created"]) == 1
    opportunity = body["created"][0]
    # (15.00 - 12.50) x the 80 units Ridgeway bought at the higher rate.
    assert opportunity["potential_value"] == "200.00"
    assert sorted(opportunity["deal_ids"]) == sorted([cheap, dear])
    assert "Ridgeway Fasteners" in opportunity["title"]

    again = client.post("/api/portfolio/analysis", headers=headers).json()
    assert again["created"] == [] and len(again["updated"]) == 1, "re-running updates rather than duplicating"
    assert len(client.get("/api/portfolio/opportunities", headers=headers).json()) == 1

    realized = client.patch(f"/api/portfolio/opportunities/{opportunity['id']}", headers=headers, json={"status": "Realized"}).json()
    assert realized["realized_value"] == "200.00", "realizing defaults to the modelled value"
    assert client.patch(f"/api/portfolio/opportunities/{opportunity['id']}", headers=headers, json={"status": "Nope"}).status_code == 422


@requires_db
def test_one_company_with_a_sku_is_not_an_arbitrage_finding(client, tenant_factory, import_store):
    headers, _, _ = tenant_factory()
    only = client.post("/api/deals", headers=headers, json={"name": "Northfield"}).json()["id"]
    commit_import(client, headers, only, [PURCHASES_CHEAP])
    body = client.post("/api/portfolio/analysis", headers=headers).json()
    assert body["skus_compared"] == 0 and body["created"] == []


@requires_db
def test_portfolio_is_not_readable_across_tenants(client, tenant_factory):
    ours, _, _ = tenant_factory()
    theirs, _, _ = tenant_factory()
    deal = client.post("/api/deals", headers=ours, json={"name": "Ours"}).json()["id"]
    client.post("/api/portfolio/tasks", headers=ours, json={"deal_id": deal, "title": "Private"})

    assert client.get("/api/portfolio/companies", headers=theirs).json() == []
    assert client.get("/api/portfolio/tasks", headers=theirs).json() == []
    assert client.get(f"/api/portfolio/companies/{deal}", headers=theirs).status_code == 404
    assert client.get("/api/portfolio/companies", headers=None).status_code == 401
