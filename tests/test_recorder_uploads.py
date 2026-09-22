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
from vista.jobs.worker import process_one
from vista.models.platform import FirmCompany, Job, Tenant, User
from vista.models.tenant import AgentRun, AgentRunEvent, DealMembership, RecorderReport, UsageEvent

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


def test_unprocessed_submission_is_private_idempotent_and_does_not_enqueue_analysis_before_acceptance(client, workspace, store):
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
    assert accepted["analysis_status"] == "queued" and accepted["publication_status"] == "draft" and accepted["report"] is None
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


BUSY_ACTIVITY = json.dumps(
    {
        "schema_version": 1,
        "events": [
            {
                "timestamp": f"2026-09-19T09:0{m}:00Z",
                "event_type": "copy" if m % 2 == 0 else "paste",
                "app": "Excel" if m % 2 == 0 else "Portal",
                "count": 1,
            }
            for m in range(0, 10)
        ]
        + [{"timestamp": "2026-09-19T09:09:30Z", "event_type": "key", "app": "Excel", "count": 25}],
    }
).encode()


def busy_body(workspace_id, kind="deal"):
    manifest = body(workspace_id, kind)
    manifest["artifacts"][0] = artifact("activity", BUSY_ACTIVITY)
    return manifest


def member_of(client, tenant_id, deal_id, role, email):
    key = uuid.uuid4().hex + uuid.uuid4().hex
    with platform_session() as session:
        user = User(tenant_id=uuid.UUID(tenant_id), email=email, api_token=key, role="member")
        session.add(user)
        session.flush()
        user_id = user.id
        schema = session.get(Tenant, uuid.UUID(tenant_id)).schema_name
        session.commit()
    with tenant_session(schema) as session:
        session.add(DealMembership(deal_id=uuid.UUID(deal_id), user_id=user_id, role=role))
        session.commit()
    return {"Authorization": f"Bearer {key}"}, schema


def test_accepted_upload_is_analysed_by_the_worker_into_a_private_draft_report(client, workspace, store):
    headers, tenant_id, owner_id, wid = workspace
    submission = create(client, headers, busy_body(wid))
    upload_files(client, headers, submission, store, activity=BUSY_ACTIVITY)
    accepted = client.post(f"{ROOT}/{submission['id']}/complete", headers=headers).json()
    assert accepted["analysis_status"] == "queued" and accepted["analysis_run_id"]
    with platform_session() as session:
        jobs = session.scalars(select(Job).where(Job.kind == "analyze_submission")).all()
        mine = [j for j in jobs if j.payload["submission_id"] == submission["id"]]
        assert len(mine) == 1 and mine[0].payload["run_id"] == accepted["analysis_run_id"]
        schema = session.get(Tenant, uuid.UUID(tenant_id)).schema_name
    # Completing again is idempotent: the same run, no second job.
    assert client.post(f"{ROOT}/{submission['id']}/complete", headers=headers).json()["analysis_run_id"] == accepted["analysis_run_id"]
    assert client.post(f"{ROOT}/{submission['id']}/analyze", headers=headers).json()["analysis_run_id"] == accepted["analysis_run_id"]
    with platform_session() as session:
        jobs = session.scalars(select(Job).where(Job.kind == "analyze_submission")).all()
        assert len([j for j in jobs if j.payload["submission_id"] == submission["id"]]) == 1

    while process_one():
        pass

    detail = client.get(f"{ROOT}/{submission['id']}", headers=headers).json()
    assert detail["analysis_status"] == "succeeded", detail
    assert detail["publication_status"] == "draft"
    report = detail["report"]
    assert report["status"] == "draft" and report["run_id"] == accepted["analysis_run_id"]
    assert report["coverage"]["documents"] == 1 and "window_title" in report["coverage"]["excluded"]
    assert [a["app"] for a in report["observed"]["apps"]][:2] in (["Excel", "Portal"], ["Portal", "Excel"])
    assert report["observed"]["transfers"][0] == {"from": "Excel", "to": "Portal", "count": 5, "mean_latency_s": 60.0}
    assert report["observed"]["switches"] == 10
    assert report["interpretation"]["source"] == "stub" and report["interpretation"]["workflows"] == []
    assert report["interpretation"]["documents"] == [{"filename": "invoice.csv", "summary": {"kind": "table", "rows": 2, "columns": 2}}]
    assert report["questions"] and all(q["answer"] is None for q in report["questions"])
    assert report["questions_open"] == len(report["questions"]) == report["questions_total"]
    assert any(q["about"].get("transfer") == ["Excel", "Portal"] for q in report["questions"])
    with tenant_session(schema) as session:
        run = session.get(AgentRun, uuid.UUID(accepted["analysis_run_id"]))
        assert run.status == "succeeded" and run.run_type == "submission_analysis" and run.agent_key == "recording_reviewer"
        assert str(run.deal_id) == wid and run.company == "Recorder Company" and str(run.requested_by) == owner_id
        events = session.scalars(select(AgentRunEvent).where(AgentRunEvent.run_id == run.id).order_by(AgentRunEvent.seq)).all()
        assert [e.event_type for e in events] == ["step", "tool_call", "tool_call", "tool_call", "model_call", "result"]
        assert events[0].data["message"] == "started" and events[-1].data["questions"] == len(report["questions"])
        assert session.scalar(select(func.count()).select_from(UsageEvent).where(UsageEvent.run_id == run.id)) == 1
        assert session.scalar(select(func.count()).select_from(RecorderReport)) >= 1
    # The run is visible in the company workspace's run ledger, the draft report is not.
    assert any(r["id"] == accepted["analysis_run_id"] for r in client.get("/api/runs", headers=headers).json())
    assert client.get("/api/recorder/reports", headers=headers).json() == []
    assert client.get(f"/api/recorder/reports/{report['id']}", headers=headers).json()["id"] == report["id"]  # the owner may read it
    viewer, _ = member_of(client, tenant_id, wid, "viewer", "viewer@example.com")
    assert client.get("/api/recorder/reports", headers=viewer).json() == []
    assert client.get(f"/api/recorder/reports/{report['id']}", headers=viewer).status_code == 404
    assert client.post(f"{ROOT}/{submission['id']}/answers", headers=viewer, json={"answers": {"q1": "x"}}).status_code == 404
    assert client.post(f"{ROOT}/{submission['id']}/publish", headers=viewer, json={"consent": True}).status_code == 404


def test_answers_and_explicit_publication_make_a_report_visible_to_the_workspace(client, workspace, store):
    headers, tenant_id, _, wid = workspace
    submission = create(client, headers, busy_body(wid))
    upload_files(client, headers, submission, store, activity=BUSY_ACTIVITY)
    client.post(f"{ROOT}/{submission['id']}/complete", headers=headers)
    answers = f"{ROOT}/{submission['id']}/answers"
    publish = f"{ROOT}/{submission['id']}/publish"
    assert client.post(publish, headers=headers, json={"consent": True}).status_code == 409  # nothing analysed yet
    while process_one():
        pass
    detail = client.get(f"{ROOT}/{submission['id']}", headers=headers).json()
    first = detail["report"]["questions"][0]["id"]
    assert client.post(answers, headers=headers, json={"answers": {"zz": "no"}}).status_code == 422
    assert client.post(answers, headers=headers, json={"answers": {}}).status_code == 422
    answered = client.post(answers, headers=headers, json={"answers": {first: "  Month-end vendor statements.  "}}).json()
    q = next(q for q in answered["report"]["questions"] if q["id"] == first)
    assert q["answer"] == "Month-end vendor statements." and q["answered_at"]
    assert answered["report"]["questions_open"] == answered["report"]["questions_total"] - 1
    assert client.post(publish, headers=headers, json={}).status_code == 422
    assert client.post(publish, headers=headers, json={"consent": False}).status_code == 422
    published = client.post(publish, headers=headers, json={"consent": True}).json()
    assert published["publication_status"] == "published" and published["report"]["published_at"]
    assert client.post(publish, headers=headers, json={"consent": True}).json() == published
    assert client.post(answers, headers=headers, json={"answers": {first: "changed"}}).status_code == 409
    # Re-analysis never replaces a published report.
    assert client.post(f"{ROOT}/{submission['id']}/analyze", headers=headers).json()["analysis_run_id"] == published["analysis_run_id"]
    listed = client.get(ROOT, headers=headers).json()
    assert listed[0]["publication_status"] == "published" and "observed" not in listed[0]["report"]

    viewer, _ = member_of(client, tenant_id, wid, "viewer", "viewer2@example.com")
    reports = client.get("/api/recorder/reports", headers=viewer).json()
    assert [r["id"] for r in reports] == [published["report"]["id"]]
    assert reports[0]["status"] == "published" and "observed" not in reports[0]
    full = client.get(f"/api/recorder/reports/{published['report']['id']}", headers=viewer).json()
    assert full["observed"]["switches"] == 10 and full["questions"][0]["answer"] == "Month-end vendor statements."
    assert full["coverage"]["excluded"]
    # Another company sees nothing; a stranger inside the tenant with no deal role sees nothing.
    other, _, _ = tenant_factory_headers(client)
    assert client.get("/api/recorder/reports", headers=other).json() == []
    assert client.get(f"/api/recorder/reports/{published['report']['id']}", headers=other).status_code == 404
    with platform_session() as session:
        key = uuid.uuid4().hex + uuid.uuid4().hex
        session.add(User(tenant_id=uuid.UUID(tenant_id), email="nobody@example.com", api_token=key, role="member"))
        session.commit()
    stranger = {"Authorization": f"Bearer {key}"}
    assert client.get("/api/recorder/reports", headers=stranger).json() == []
    assert client.get(f"/api/recorder/reports/{published['report']['id']}", headers=stranger).status_code == 404


def tenant_factory_headers(client):
    from vista.config import settings

    name = f"other-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/tenants",
        headers={"X-Vista-Provisioning-Key": settings.provisioning_key},
        json={"name": name, "owner_email": f"owner@{name}.example.com"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return {"Authorization": f"Bearer {data['api_token']}"}, data["tenant_id"], data["owner_user_id"]


def test_failed_analysis_is_recorded_and_can_be_retried(client, workspace, store, monkeypatch):
    headers, tenant_id, _, wid = workspace
    submission = create(client, headers, busy_body(wid))
    upload_files(client, headers, submission, store, activity=BUSY_ACTIVITY)
    accepted = client.post(f"{ROOT}/{submission['id']}/complete", headers=headers).json()
    with platform_session() as session:
        jobs = session.scalars(select(Job).where(Job.kind == "analyze_submission")).all()
        job = next(j for j in jobs if j.payload["submission_id"] == submission["id"])
        job.max_attempts = 1
        session.commit()
    # The object store loses the artifact between acceptance and analysis.
    store.fail = True
    while process_one():
        pass
    store.fail = False
    failed = client.get(f"{ROOT}/{submission['id']}", headers=headers).json()
    assert failed["analysis_status"] == "failed" and "could not be read" in failed["analysis_error"] and failed["report"] is None
    assert client.post(f"{ROOT}/{submission['id']}/publish", headers=headers, json={"consent": True}).status_code == 409
    retried = client.post(f"{ROOT}/{submission['id']}/analyze", headers=headers).json()
    assert retried["analysis_status"] == "queued" and retried["analysis_run_id"] != accepted["analysis_run_id"]
    while process_one():
        pass
    done = client.get(f"{ROOT}/{submission['id']}", headers=headers).json()
    assert done["analysis_status"] == "succeeded" and done["report"]["run_id"] == retried["analysis_run_id"]
    with platform_session() as session:
        schema = session.get(Tenant, uuid.UUID(tenant_id)).schema_name
    with tenant_session(schema) as session:
        first = session.get(AgentRun, uuid.UUID(accepted["analysis_run_id"]))
        assert first.status == "failed" and first.error
