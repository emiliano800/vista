import base64
import copy
import hashlib
import io
import json
import uuid

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from sqlalchemy import func, select

from tests.conftest import requires_db
from tests.test_portfolio import make_firm
from vista.db import platform_session, tenant_session
from vista.models.platform import FirmCompany, Job, Tenant, User
from vista.models.tenant import DealMembership

pytestmark = requires_db

ROOT = "/api/recorder/submissions"
ACTIVITY = json.dumps(
    {"schema_version": 1, "events": [{"timestamp": "2026-09-19T09:01:00Z", "event_type": "click", "app": "Accounting", "count": 1}]}
).encode()
DOCUMENT = b"invoice,total\nINV-1001,125.00\n"


def artifact(artifact_id, data, kind="activity", filename="activity.json", content_type="application/json"):
    return {
        "id": artifact_id,
        "kind": kind,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def body(workspace_id, kind="deal"):
    return {
        "format_version": 2,
        "sharing_policy": "activity-metadata-v1",
        "consent": True,
        "device_id": str(uuid.uuid4()),
        "source_id": f"session-{uuid.uuid4().hex}",
        "workspace": {"id": workspace_id, "kind": kind},
        "started_at": "2026-09-19T09:00:00Z",
        "ended_at": "2026-09-19T09:10:00Z",
        "active_seconds": 600,
        "artifacts": [artifact("activity", ACTIVITY), artifact("document-abcdef123456", DOCUMENT, "document", "invoice.csv", "text/csv")],
    }


@pytest.fixture()
def workspace(client, tenant_factory):
    headers, tenant_id, owner_id = tenant_factory()
    response = client.post("/deals", json={"name": "Recorder Company"}, headers=headers)
    assert response.status_code == 201
    return headers, tenant_id, owner_id, response.json()["id"]


@pytest.fixture()
def store(monkeypatch):
    class Store:
        def __init__(self):
            self.objects = {}
            self.signed = {}
            self.fail = False

        def generate_presigned_url(self, method, Params, ExpiresIn):
            self.signed[Params["Key"]] = Params
            return "https://storage.example.test/" + Params["Key"]

        def get_object(self, *, Bucket, Key):
            if self.fail:
                raise EndpointConnectionError(endpoint_url="http://unavailable")
            if Key not in self.objects:
                raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
            data = self.objects[Key]
            return {"Body": io.BytesIO(data), "ContentLength": len(data), "VersionId": "v1"}

    fake = Store()
    monkeypatch.setattr("vista.recorder_uploads.s3_client", lambda: fake)
    return fake


def create(client, headers, manifest):
    response = client.post(ROOT, headers=headers, json=manifest)
    assert response.status_code == 201, response.text
    return response.json()


def upload_files(client, headers, submission, store, activity=ACTIVITY):
    response = client.post(f"{ROOT}/{submission['id']}/upload-urls", headers=headers)
    assert response.status_code == 200, response.text
    for upload in response.json()["uploads"]:
        key = upload["url"].split("storage.example.test/", 1)[1]
        store.objects[key] = activity if upload["artifact_id"] == "activity" else DOCUMENT
    return response.json()["uploads"]


def test_legacy_workspaces_are_resolved_from_memberships(client, workspace):
    headers, tenant_id, owner_id, wid = workspace
    response = client.get("/api/recorder/workspaces", headers=headers)
    assert response.status_code == 200, response.text
    identity = response.json()
    assert identity["tenant_id"] == tenant_id and identity["user_id"] == owner_id
    assert identity["workspaces"] == [{"id": wid, "kind": "deal", "name": "Recorder Company", "canonical_company_id": None}]
    assert client.get("/api/recorder/workspaces").status_code == 401


def test_canonical_company_is_resolved_by_tenant_not_name(client):
    analyst, _ = make_firm()
    company = client.post("/api/portfolio/companies", headers=analyst, json={"name": "Canonical Company"}).json()
    with platform_session() as session:
        row = session.get(FirmCompany, uuid.UUID(company["id"]))
        key = uuid.uuid4().hex + uuid.uuid4().hex
        user = User(tenant_id=row.tenant_id, email="employee@example.com", api_token=key, role="member")
        session.add(user)
        session.commit()
    headers = {"Authorization": f"Bearer {key}"}
    scopes = client.get("/api/recorder/workspaces", headers=headers).json()["workspaces"]
    assert scopes == [{"id": company["id"], "kind": "company", "name": "Canonical Company", "canonical_company_id": company["id"]}]
    submission = create(client, headers, body(company["id"], "company"))
    assert submission["workspace"] == {"id": company["id"], "kind": "company"}
    assert submission["canonical_company_id"] == company["id"]
    assert client.post(ROOT, headers=analyst, json=body(company["id"], "company")).status_code == 404


def test_unprocessed_submission_is_private_idempotent_and_does_not_enqueue_analysis(client, workspace, store):
    headers, _, _, wid = workspace
    manifest = body(wid)
    with platform_session() as session:
        before = session.scalar(select(func.count()).select_from(Job))
    submission = create(client, headers, manifest)
    assert submission["upload_status"] == "uploading"
    assert submission["analysis_status"] == "not_started"
    assert submission["publication_status"] == "draft"
    assert submission["receipt"] is None
    assert create(client, headers, manifest)["id"] == submission["id"]
    modified = copy.deepcopy(manifest)
    modified["active_seconds"] = 599
    assert client.post(ROOT, headers=headers, json=modified).status_code == 409
    assert client.get(ROOT, headers=headers).json() == [submission]
    assert client.get(f"/api/deals/{wid}/recordings", headers=headers).json() == []
    with platform_session() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == before


def test_completion_requires_matching_objects_and_returns_an_idempotent_receipt(client, workspace, store):
    headers, _, _, wid = workspace
    manifest = body(wid)
    submission = create(client, headers, manifest)
    complete = f"{ROOT}/{submission['id']}/complete"
    assert client.post(complete, headers=headers).status_code == 409
    uploads = upload_files(client, headers, submission, store)
    for upload, spec in zip(uploads, manifest["artifacts"], strict=True):
        params = store.signed[upload["url"].split("storage.example.test/", 1)[1]]
        assert params["ChecksumSHA256"] == base64.b64encode(bytes.fromhex(spec["sha256"])).decode()
        assert params["ContentLength"] == spec["size_bytes"]
        assert upload["headers"]["x-amz-checksum-sha256"] == params["ChecksumSHA256"]
    document_key = next(k for k in store.objects if "document-" in k)
    store.objects[document_key] = b"x" * len(DOCUMENT)
    assert client.post(complete, headers=headers).status_code == 409
    assert client.get(f"{ROOT}/{submission['id']}", headers=headers).json()["receipt"] is None
    store.objects[document_key] = DOCUMENT
    accepted = client.post(complete, headers=headers)
    assert accepted.status_code == 200, accepted.text
    accepted = accepted.json()
    assert accepted["upload_status"] == "accepted"
    assert accepted["analysis_status"] == "not_started" and accepted["publication_status"] == "draft"
    assert accepted["receipt"]["manifest_hash"] == submission["manifest_hash"]
    assert accepted["receipt"]["artifact_count"] == 2
    assert accepted["receipt"]["verified_at"]
    assert client.post(complete, headers=headers).json() == accepted
    assert client.post(f"{ROOT}/{submission['id']}/upload-urls", headers=headers).json()["uploads"] == []


def test_other_users_cannot_inspect_a_private_draft_even_in_the_same_workspace(client, workspace, store, tenant_factory):
    headers, tenant_id, _, wid = workspace
    submission = create(client, headers, body(wid))
    key = uuid.uuid4().hex + uuid.uuid4().hex
    with platform_session() as session:
        user = User(tenant_id=uuid.UUID(tenant_id), email="another@example.com", api_token=key, role="admin")
        session.add(user)
        session.flush()
        user_id = user.id
        schema = session.get(Tenant, uuid.UUID(tenant_id)).schema_name
        session.commit()
    with tenant_session(schema) as session:
        session.add(DealMembership(deal_id=uuid.UUID(wid), user_id=user_id, role="owner"))
        session.commit()
    same_company = {"Authorization": f"Bearer {key}"}
    other_company, _, _ = tenant_factory()
    for other in (same_company, other_company):
        assert client.get(ROOT, headers=other).json() == []
        assert client.get(f"{ROOT}/{submission['id']}", headers=other).status_code == 404
        assert client.post(f"{ROOT}/{submission['id']}/upload-urls", headers=other).status_code == 404
        assert client.post(f"{ROOT}/{submission['id']}/complete", headers=other).status_code == 404
    assert client.post(ROOT, headers=other_company, json=body(wid)).status_code == 404


def test_workspace_revocation_blocks_uploads_and_receipt_access(client, workspace, store):
    headers, tenant_id, owner_id, wid = workspace
    submission = create(client, headers, body(wid))
    with platform_session() as session:
        schema = session.get(Tenant, uuid.UUID(tenant_id)).schema_name
    with tenant_session(schema) as session:
        membership = session.scalar(
            select(DealMembership).where(DealMembership.deal_id == uuid.UUID(wid), DealMembership.user_id == uuid.UUID(owner_id))
        )
        membership.role = "viewer"
        session.commit()
    assert client.get("/api/recorder/workspaces", headers=headers).json()["workspaces"] == []
    assert client.get(f"{ROOT}/{submission['id']}", headers=headers).status_code == 404
    assert client.post(f"{ROOT}/{submission['id']}/upload-urls", headers=headers).status_code == 404
    assert client.post(f"{ROOT}/{submission['id']}/complete", headers=headers).status_code == 404


@pytest.mark.parametrize("change", [{"consent": False}, {"sharing_policy": "all-files"}, {"summary": {}}, {"active_seconds": 601}])
def test_invalid_or_unapproved_package_is_rejected(client, workspace, change):
    headers, _, _, wid = workspace
    assert client.post(ROOT, headers=headers, json={**body(wid), **change}).status_code == 422
    assert client.get(ROOT, headers=headers).json() == []


@pytest.mark.parametrize(
    "change",
    [
        {"filename": "../invoice.csv"},
        {"kind": "video", "filename": "screen.webm"},
        {"size_bytes": 50 * 1024 * 1024},
        {"sha256": "not-a-hash"},
        {"content_type": "image/jpeg"},
    ],
)
def test_artifact_allowlist_and_limits(client, workspace, change):
    headers, _, _, wid = workspace
    manifest = body(wid)
    manifest["artifacts"][1].update(change)
    assert client.post(ROOT, headers=headers, json=manifest).status_code == 422


def test_activity_content_must_be_metadata_only(client, workspace, store):
    headers, _, _, wid = workspace
    data = json.loads(ACTIVITY)
    data["events"][0]["text"] = "typed content must never be in this format"
    unsafe = json.dumps(data).encode()
    manifest = body(wid)
    manifest["artifacts"][0] = artifact("activity", unsafe)
    submission = create(client, headers, manifest)
    upload_files(client, headers, submission, store, activity=unsafe)
    response = client.post(f"{ROOT}/{submission['id']}/complete", headers=headers)
    assert response.status_code == 422
    assert "typed content" not in response.text
    assert client.get(f"{ROOT}/{submission['id']}", headers=headers).json()["receipt"] is None


def test_storage_outage_leaves_submission_retryable(client, workspace, store):
    headers, _, _, wid = workspace
    submission = create(client, headers, body(wid))
    upload_files(client, headers, submission, store)
    store.fail = True
    assert client.post(f"{ROOT}/{submission['id']}/complete", headers=headers).status_code == 503
    store.fail = False
    assert client.post(f"{ROOT}/{submission['id']}/complete", headers=headers).json()["upload_status"] == "accepted"


def test_real_local_storage_accepts_checksum_bound_signed_uploads(client, workspace, monkeypatch):
    from urllib.parse import urlparse

    import httpx

    from vista.config import settings
    from vista.storage import s3_client

    endpoint = settings.s3_endpoint_url
    if not endpoint or urlparse(endpoint).hostname not in ("localhost", "127.0.0.1"):
        pytest.skip("This integration test only uses local object storage")
    try:
        available = httpx.get(f"{endpoint}/minio/health/live", timeout=1).status_code == 200
    except httpx.HTTPError:
        available = False
    if not available:
        pytest.skip("Local MinIO is not running")
    monkeypatch.setattr(settings, "s3_bucket", f"vista-recorder-test-{uuid.uuid4().hex}")
    s3_client().create_bucket(Bucket=settings.s3_bucket)
    headers, _, _, wid = workspace
    manifest = body(wid)
    submission = create(client, headers, manifest)
    response = client.post(f"{ROOT}/{submission['id']}/upload-urls", headers=headers)
    assert response.status_code == 200
    for upload in response.json()["uploads"]:
        data = ACTIVITY if upload["artifact_id"] == "activity" else DOCUMENT
        sent = httpx.put(upload["url"], headers=upload["headers"], content=data, timeout=10)
        assert sent.status_code == 200, "Local object storage rejected a checksum-bound upload"
    response = client.post(f"{ROOT}/{submission['id']}/complete", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["upload_status"] == "accepted"
    assert response.json()["receipt"]["artifact_count"] == 2
