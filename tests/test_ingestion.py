import base64
import copy
import io
import uuid
from datetime import date, datetime
from pathlib import Path

import pytest
from botocore.exceptions import EndpointConnectionError
from openpyxl import Workbook

from tests.conftest import requires_db
from vista.db import platform_session, tenant_session
from vista.ingestion import analyze, apply_mappings, parse_files
from vista.models.platform import Tenant
from vista.models.tenant import DealMembership

SAMPLE = Path(__file__).resolve().parents[1] / "src/web/public/demo/meridian"


def file(name, content):
    return {"name": name, "content": base64.b64encode(content.encode() if isinstance(content, str) else content).decode()}


def demo_files():
    return [file(p.name, p.read_bytes()) for p in sorted(SAMPLE.glob("*.csv"))]


def mapped(files):
    tables = parse_files(files)
    return apply_mappings(tables, [{"id": t["id"], "kind": t["kind"], "mapping": t["mapping"]} for t in tables])


def test_meridian_finding_is_calculated_and_cites_actual_rows():
    tables = mapped(demo_files())
    result = analyze(tables, date(2026, 3, 31))
    assert result["summary"]["records"] == 246
    assert result["summary"]["commission_variance"] == "1584.48"
    assert result["summary"]["matched_commissions"] == 31
    finding = next(f for f in result["findings"] if f["category"] == "commission")
    assert finding["calculation"] == {"premium": "79224", "rate": "12.0", "expected": "9506.88", "paid": "7922.4", "difference": "1584.48"}
    for evidence in finding["evidence"]:
        table = next(t for t in tables if t["id"] == evidence["table_id"])
        row = next(r for r in table["records"] if r["row"] == evidence["row"])
        assert row["values"] == evidence["values"]
        assert row["values"]["policy_number"] == "LIB-AUTO-3288560"
    # Do not trust the planted variance/expected values; changing them must not affect arithmetic.
    commission_table = next(t for t in tables if t["kind"] == "commissions")
    for row in commission_table["records"]:
        row["values"]["variance"] = "999999"
        row["values"]["expected_commission"] = "0"
    assert analyze(tables, date(2026, 3, 31))["summary"]["commission_variance"] == "1584.48"


def test_ambiguous_policies_and_duplicate_statements_do_not_inflate_recovery():
    tables = mapped(demo_files())
    policy_table = next(t for t in tables if t["kind"] == "policies")
    duplicate = copy.deepcopy(next(r for r in policy_table["records"] if r["values"]["policy_number"] == "LIB-AUTO-3288560"))
    duplicate["row"] = 999
    policy_table["records"].append(duplicate)
    result = analyze(tables, date(2026, 3, 31))
    assert result["summary"]["commission_variance"] == "0"
    assert any("more than once" in f["title"] for f in result["findings"])
    tables = mapped(demo_files())
    statements = next(t for t in tables if t["kind"] == "commissions")
    statements["records"].append(
        copy.deepcopy(next(r for r in statements["records"] if r["values"]["policy_number"] == "LIB-AUTO-3288560"))
    )
    assert analyze(tables, date(2026, 3, 31))["summary"]["commission_variance"] == "0"


def test_overdue_review_respects_snapshot_date_and_payment_plans():
    tables = mapped(
        [
            file(
                "invoices.csv",
                "invoice_id,balance,due_date,payment_plan\n"
                "I-1,100,2026-01-01,Full Pay\nI-2,500,2026-01-01,Quarterly\nI-3,0,2026-01-01,Full Pay\n",
            )
        ]
    )
    assert not analyze(tables, date(2026, 1, 31))["findings"]
    findings = analyze(tables, date(2026, 3, 31))["findings"]
    assert len(findings) == 1 and findings[0]["amount"] == "100"
    assert findings[0]["evidence"][0]["row"] == 2


def test_alias_mapping_and_quoted_csv_preserve_source():
    tables = mapped([file("policy.csv", 'Policy No,Commission Rate,client_name\nPOL-1,12,"Jones, Inc."\n')])
    assert tables[0]["mapping"]["policy_number"] == "Policy No"
    assert tables[0]["records"][0]["values"]["client_name"] == "Jones, Inc."


@pytest.mark.parametrize("content", ["a,a\n1,2", "a,b\n1,2,3", "a,\n1,2", "a\n" + "x" * 513])
def test_invalid_table_structure_is_rejected(content):
    with pytest.raises(ValueError):
        parse_files([file("bad.csv", content)])


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "abc", "1e999999"])
def test_invalid_numeric_values_are_not_coerced_to_zero(amount):
    with pytest.raises(ValueError):
        mapped([file("policy.csv", f"policy_number,commission_pct\nP-1,{amount}\n")])


def test_workbook_dates_percentages_and_formula_rejection():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Invoices"
    sheet.append(["invoice_id", "balance", "due_date", "payment_plan"])
    sheet.append(["I-1", 100, datetime(2026, 1, 1), "Full Pay"])
    rates = workbook.create_sheet("Policies")
    rates.append(["policy_number", "commission_pct"])
    rates.append(["P-1", 0.12])
    rates["B2"].number_format = "0%"
    stream = io.BytesIO()
    workbook.save(stream)
    tables = mapped([file("book.xlsx", stream.getvalue())])
    assert tables[0]["records"][0]["values"]["due_date"] == "2026-01-01"
    assert tables[1]["records"][0]["values"]["commission_pct"] == "12.00%"
    sheet["B2"] = "=100+1"
    stream = io.BytesIO()
    workbook.save(stream)
    with pytest.raises(ValueError, match="export formulas"):
        parse_files([file("book.xlsx", stream.getvalue())])


@pytest.fixture()
def import_store(monkeypatch):
    class Store:
        def __init__(self):
            self.items = {}
            self.fail = False

        def put_object(self, **kwargs):
            if self.fail:
                raise EndpointConnectionError(endpoint_url="http://unavailable")
            self.items[kwargs["Key"]] = kwargs["Body"]

    store = Store()
    monkeypatch.setattr("vista.api.imports.s3_client", lambda: store)
    return store


@requires_db
def test_saved_import_mapping_review_export_and_permissions(client, tenant_factory, import_store):
    headers, tenant_id, user_id = tenant_factory()
    deal = client.post("/api/deals", headers=headers, json={"name": "Meridian"}).json()["id"]
    url = f"/api/deals/{deal}/imports"
    body = {"files": demo_files(), "as_of": "2026-03-31"}
    first = client.post(url, headers=headers, json=body)
    assert first.status_code == 201, first.text
    batch = first.json()
    assert batch["status"] == "preview" and not batch["analysis"]
    assert len(import_store.items) == 1
    assert client.post(url, headers=headers, json=body).json()["id"] == batch["id"]
    assert len(import_store.items) == 1
    choices = {"tables": [{k: t[k] for k in ["id", "kind", "mapping"]} for t in batch["tables"]]}
    path = f"/api/imports/{batch['id']}"
    bad = copy.deepcopy(choices)
    bad["tables"][0]["mapping"] = {}
    assert client.post(path + "/commit", headers=headers, json=bad).status_code == 422
    assert client.get(path, headers=headers).json()["status"] == "preview"
    committed = client.post(path + "/commit", headers=headers, json=choices)
    assert committed.status_code == 200, committed.text
    saved = committed.json()
    assert saved["analysis"]["summary"]["commission_variance"] == "1584.48"
    assert len(client.post(path + "/commit", headers=headers, json=choices).json()["events"]) == 2
    finding = saved["analysis"]["findings"][0]
    review_url = path + "/findings/" + finding["id"]
    reviewed = client.post(review_url, headers=headers, json={"status": "reviewed"})
    assert reviewed.status_code == 200
    assert reviewed.json()["analysis"]["findings"][0]["status"] == "reviewed"
    assert len(reviewed.json()["events"]) == 3
    assert len(client.post(review_url, headers=headers, json={"status": "reviewed"}).json()["events"]) == 3
    assert client.get(path + "/export", headers=headers).json()["events"][-1]["status"] == "reviewed"
    assert client.get(url, headers=headers).json()["imports"][0]["status"] == "completed"
    other, _, _ = tenant_factory()
    assert client.get(path, headers=other).status_code == 404
    assert client.post(url, headers=other, json=body).status_code == 403
    assert client.get(url).status_code == 401
    with platform_session() as session:
        schema = session.get(Tenant, uuid.UUID(tenant_id)).schema_name
    with tenant_session(schema) as session:
        from sqlalchemy import select

        membership = session.scalar(select(DealMembership).where(DealMembership.user_id == uuid.UUID(user_id)))
        membership.role = "viewer"
        session.commit()
    assert client.get(url, headers=headers).json()["role"] == "viewer"
    assert client.get(path, headers=headers).status_code == 200
    assert client.post(url, headers=headers, json=body).status_code == 403
    assert client.post(review_url, headers=headers, json={"status": "open"}).status_code == 403


@requires_db
def test_storage_failure_does_not_create_partial_import(client, tenant_factory, import_store):
    headers, _, _ = tenant_factory()
    deal = client.post("/api/deals", headers=headers, json={"name": "Test"}).json()["id"]
    url = f"/api/deals/{deal}/imports"
    import_store.fail = True
    response = client.post(url, headers=headers, json={"files": demo_files(), "as_of": "2026-03-31"})
    assert response.status_code == 503
    assert client.get(url, headers=headers).json()["imports"] == []
