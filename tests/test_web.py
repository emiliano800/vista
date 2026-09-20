import copy
import csv
import io
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from botocore.exceptions import EndpointConnectionError
from fastapi.testclient import TestClient

from taskmining.capture import SyntheticSource
from taskmining.pipeline import Pipeline
from tests.conftest import requires_db
from vista.auth import COOKIE
from vista.config import settings
from vista.db import platform_session, tenant_session
from vista.main import app
from vista.models.platform import BrowserSession, Tenant, User
from vista.models.tenant import DealMembership
from vista.recordings import RecordingUpload
from vista.security import token_digest


@pytest.fixture()
def bundle(tmp_path):
    result = Pipeline().run(SyntheticSource(n_cases=3, start=datetime(2026, 9, 19, 9, tzinfo=UTC)))
    result.write(tmp_path)
    start, end = min(e.timestamp for e in result.raw), max(e.timestamp for e in result.raw)
    return {
        "version": 1,
        "manifest": {
            "recording_id": "test-recording",
            "started_at": start.isoformat(),
            "ended_at": end.isoformat(),
            "active_seconds": int((end - start).total_seconds()),
            "processing": "done",
            "files": {"screen": "/private/screen.webm"},
            "user": "PRIVATE_NAME",
        },
        "summary": json.loads((tmp_path / "summary.json").read_text()),
        "event_log_csv": (tmp_path / "event_log.csv").read_text(),
    }


@pytest.fixture()
def objects(monkeypatch):
    class Store:
        def __init__(self):
            self.data = {}
            self.puts = 0
            self.fail = False

        def put_object(self, *, Bucket, Key, Body, ContentType):
            if self.fail:
                raise EndpointConnectionError(endpoint_url="http://unavailable")
            self.puts += 1
            self.data[Key] = Body

        def get_object(self, *, Bucket, Key):
            if self.fail:
                raise EndpointConnectionError(endpoint_url="http://unavailable")
            return {"Body": io.BytesIO(self.data[Key])}

    store = Store()
    monkeypatch.setattr("vista.api.recordings.s3_client", lambda: store)
    return store


def company(client, headers, name="Harbor Heating"):
    response = client.post("/api/deals", headers=headers, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_pipeline_report_contract_and_private_manifest_fields(bundle):
    clean = RecordingUpload.model_validate(bundle).model_dump(mode="json")
    assert "files" not in clean["manifest"] and "user" not in clean["manifest"]
    assert clean["summary"]["n_steps"] > 0


@pytest.mark.parametrize("mutation", ["steps", "candidate", "columns", "nonfinite", "unfinished"])
def test_reject_invalid_reports(bundle, mutation):
    if mutation == "steps":
        bundle["summary"]["n_steps"] += 1
    elif mutation == "candidate":
        bundle["summary"]["automation_potential"][0]["activity"] = "No evidence"
    elif mutation == "columns":
        bundle["event_log_csv"] = "activity,secret\nA,secret\n"
    elif mutation == "nonfinite":
        bundle["summary"]["automation_potential"][0]["score"] = float("nan")
    else:
        bundle["manifest"]["processing"] = "running"
    with pytest.raises(ValueError):
        RecordingUpload.model_validate(bundle)


@requires_db
def test_browser_session_csrf_logout_rotation_and_hash_storage(client, tenant_factory, monkeypatch):
    headers, _, user_id = tenant_factory()
    token = headers["Authorization"].split()[1]
    with platform_session() as session:
        assert session.get(User, uuid.UUID(user_id)).api_token_hash == token_digest(token)
    with TestClient(app, base_url="https://testserver") as browser:
        monkeypatch.setattr(settings, "cookie_secure", True)
        assert browser.get("/api/auth/me").status_code == 401
        assert browser.post("/api/auth/session", json={"token": token}).status_code == 403
        assert (
            browser.post(
                "/api/auth/session", headers={"X-Vista-Request": "1", "Origin": "https://evil.example"}, json={"token": token}
            ).status_code
            == 403
        )
        response = browser.post(
            "/api/auth/session", headers={"X-Vista-Request": "1", "Origin": "https://testserver"}, json={"token": token}
        )
        assert response.status_code == 200
        assert all(flag in response.headers["set-cookie"].lower() for flag in ("httponly", "secure", "samesite=lax"))
        cookie = browser.cookies.get(COOKIE)
        with platform_session() as session:
            saved = session.get(BrowserSession, token_digest(cookie))
            assert saved is not None and saved.key_hash == token_digest(token)
        assert browser.get("/api/auth/me").json()["user_id"] == user_id
        assert browser.post("/api/deals", json={"name": "Rejected"}).status_code == 403
        assert browser.delete("/api/auth/session", headers={"X-Vista-Request": "1"}).status_code == 204
        assert browser.get("/api/auth/me").status_code == 401
        browser.cookies.set(COOKIE, cookie)
        assert browser.get("/api/auth/me").status_code == 401
        browser.cookies.clear()
        browser.post("/api/auth/session", headers={"X-Vista-Request": "1"}, json={"token": token})
        with platform_session() as session:
            user = session.get(User, uuid.UUID(user_id))
            user.api_token = secrets.token_hex(32)
            session.commit()
        assert browser.get("/api/auth/me").status_code == 401
        assert browser.get("/api/auth/me", headers=headers).status_code == 401


@requires_db
def test_expired_session_and_provisioning_disabled(client, tenant_factory, monkeypatch):
    headers, _, _ = tenant_factory()
    with TestClient(app) as browser:
        browser.post("/api/auth/session", headers={"X-Vista-Request": "1"}, json={"token": headers["Authorization"].split()[1]})
        with platform_session() as session:
            saved = session.get(BrowserSession, token_digest(browser.cookies.get(COOKIE)))
            saved.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()
        assert browser.get("/api/auth/me").status_code == 401
    monkeypatch.setattr(settings, "provisioning_key", None)
    assert client.post("/tenants", json={"name": "No", "owner_email": "a@b.com"}).status_code == 403


@requires_db
def test_recording_roundtrip_retry_update_evidence_and_isolation(client, tenant_factory, bundle, objects):
    headers, tenant_id, _ = tenant_factory()
    deal = company(client, headers)
    path = f"/api/deals/{deal}/recordings"
    first = client.post(path, headers=headers, json=bundle)
    assert first.status_code == 200, first.text
    record = first.json()
    rid = record["id"]
    assert len(objects.data) == 1 and "PRIVATE_NAME" not in next(iter(objects.data.values())).decode()
    assert client.post(path, headers=headers, json=bundle).json()["id"] == rid
    assert objects.puts == 1
    assert client.get(path, headers=headers).json()[0]["id"] == rid
    assert client.get(path + "?offset=1", headers=headers).json() == []
    activity = bundle["summary"]["automation_potential"][0]["activity"]
    evidence = client.get(f"/api/recordings/{rid}/evidence", headers=headers, params={"activity": activity, "limit": 2}).json()
    assert evidence["total"] >= len(evidence["rows"]) > 0
    assert all(r["activity"] == activity and r["row"] >= 1 for r in evidence["rows"])
    download = client.get(f"/api/recordings/{rid}/download", headers=headers)
    assert download.json()["event_log_csv"] == bundle["event_log_csv"]
    assert download.headers["cache-control"] == "no-store"
    changed = copy.deepcopy(bundle)
    changed["summary"]["n_open_questions"] += 1
    updated = client.post(path, headers=headers, json=changed).json()
    assert updated["id"] == rid and updated["content_hash"] != record["content_hash"]
    assert len(client.get(path, headers=headers).json()) == 1
    foreign, _, _ = tenant_factory()
    for endpoint in (path, f"/api/recordings/{rid}", f"/api/recordings/{rid}/evidence", f"/api/recordings/{rid}/download"):
        assert client.get(endpoint, headers=foreign).status_code in (403, 404)
        assert client.get(endpoint).status_code == 401
    assert client.post(path, headers=foreign, json=bundle).status_code == 403
    # Another employee of the same tenant still needs this company's membership.
    viewer_key = secrets.token_hex(32)
    with platform_session() as session:
        user = User(tenant_id=uuid.UUID(tenant_id), email="viewer@company.example", api_token=viewer_key)
        session.add(user)
        session.commit()
        tenant = session.get(Tenant, uuid.UUID(tenant_id))
    viewer = {"Authorization": "Bearer " + viewer_key}
    assert client.get(f"/api/recordings/{rid}", headers=viewer).status_code == 403
    with tenant_session(tenant.schema_name) as session:
        session.add(DealMembership(deal_id=uuid.UUID(deal), user_id=user.id, role="viewer"))
        session.commit()
    assert client.get(f"/api/recordings/{rid}/evidence", headers=viewer).status_code == 200
    assert client.post(path, headers=viewer, json=bundle).status_code == 403


@requires_db
def test_storage_failure_can_retry_without_partial_record(client, tenant_factory, bundle, objects):
    headers, _, _ = tenant_factory()
    path = f"/api/deals/{company(client, headers)}/recordings"
    objects.fail = True
    assert client.post(path, headers=headers, json=bundle).status_code == 503
    assert client.get(path, headers=headers).json() == []
    objects.fail = False
    rid = client.post(path, headers=headers, json=bundle).json()["id"]
    objects.fail = True
    assert client.get(f"/api/recordings/{rid}/evidence", headers=headers).status_code == 503
    assert client.get(f"/api/recordings/{rid}", headers=headers).status_code == 200


@requires_db
def test_csv_formula_export_is_inert_and_json_preserves_evidence(client, tenant_factory, bundle, objects):
    rows = list(csv.reader(io.StringIO(bundle["event_log_csv"])))
    rows[1][-1] = '=HYPERLINK("https://bad.example")'
    output = io.StringIO()
    csv.writer(output).writerows(rows)
    bundle["event_log_csv"] = output.getvalue()
    headers, _, _ = tenant_factory()
    rid = client.post(f"/api/deals/{company(client, headers)}/recordings", headers=headers, json=bundle).json()["id"]
    exported = client.get(f"/api/recordings/{rid}/download?format=csv", headers=headers)
    assert list(csv.reader(io.StringIO(exported.text)))[1][-1].startswith("'=")
    assert client.get(f"/api/recordings/{rid}/download", headers=headers).json()["event_log_csv"] == bundle["event_log_csv"]


def test_request_limit_and_validation_does_not_echo_secrets():
    with TestClient(app) as browser:
        response = browser.post("/api/auth/session", json={"token": "short-secret"})
        assert response.status_code == 422 and "short-secret" not in response.text
        response = browser.post("/api/auth/session", content=b"x" * (8 * 1024 * 1024 + 1))
        assert response.status_code == 413
        assert browser.get("/").status_code == 200
        for page in ("/signin/", "/account/", "/recorder/"):
            assert browser.get(page).status_code == 200, page
        assert browser.get("/account/app.js").status_code == 200
        assert browser.get("/nav.js").status_code == 200
