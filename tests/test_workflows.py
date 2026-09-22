import copy
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from tests.conftest import requires_db
from tests.test_portfolio import make_firm
from vista.db import platform_session, tenant_session
from vista.models.platform import Firm, FirmCompany, FirmMembership, Tenant, User

pytestmark = requires_db

DEFINITION = {
    "goal": "Prepare a supplier invoice draft for review",
    "required_inputs": ["invoice", "supplier_directory"],
    "allowed_tools": ["read_invoice", "lookup_supplier", "create_invoice_draft"],
    "success_criteria": ["The draft matches the invoice number, supplier, currency and total"],
    "environment": "sandbox",
    "limits": {"max_steps": 10, "max_runtime_seconds": 300, "max_cost_usd": "1.00"},
}


def member(firm_id, role):
    key = uuid.uuid4().hex + uuid.uuid4().hex
    with platform_session() as session:
        firm = session.get(Firm, uuid.UUID(firm_id))
        user = User(tenant_id=firm.home_tenant_id, email=f"{uuid.uuid4().hex}@example.com", api_token=key, role="member")
        session.add(user)
        session.flush()
        session.add(FirmMembership(firm_id=firm.id, user_id=user.id, role=role))
        session.commit()
        return {"Authorization": f"Bearer {key}"}, user.id


@pytest.fixture()
def workspace(client):
    headers, firm_id = make_firm(role="admin")
    response = client.post("/api/portfolio/companies", headers=headers, json={"name": "Workflow Test Company"})
    assert response.status_code == 201, response.text
    return headers, firm_id, response.json()["id"]


def create_workflow(client, headers, company_id):
    response = client.post(
        f"/api/companies/{company_id}/workflows",
        headers=headers,
        json={"name": "Supplier invoice drafts", "definition": DEFINITION},
    )
    assert response.status_code == 201, response.text
    return response.json()


def workflow_path(workflow):
    return f"/api/companies/{workflow['company_id']}/workflows/{workflow['id']}"


def version_path(workflow, version_id=None):
    return f"{workflow_path(workflow)}/versions/{version_id or workflow['latest_version']['id']}"


def decide(client, headers, workflow, decision="approved", reason="Reviewed sandbox scope", version_id=None):
    return client.post(
        f"{version_path(workflow, version_id)}/decision",
        headers=headers,
        json={"decision": decision, "reason": reason},
    )


def test_workflow_starts_as_an_ineligible_draft(client, workspace):
    headers, _, cid = workspace
    workflow = create_workflow(client, headers, cid)
    version = workflow["latest_version"]
    assert workflow["company_id"] == cid
    assert version["number"] == 1
    assert version["status"] == "draft"
    assert version["decision"] is None
    assert version["definition"] == DEFINITION
    assert len(version["definition_hash"]) == 64
    assert version["created_by"] and version["created_at"]
    assert client.get(workflow_path(workflow), headers=headers).json() == workflow
    assert client.get(f"/api/companies/{cid}/workflows", headers=headers).json() == [workflow]
    assert client.get(f"{workflow_path(workflow)}/versions", headers=headers).json() == [version]
    result = client.get(f"{version_path(workflow)}/eligibility", headers=headers).json()
    assert result["eligible"] is False
    assert result["reasons"] == ["version_not_approved"]
    assert result["execution_available"] is False


def test_admin_approval_is_version_bound_and_idempotent(client, workspace):
    headers, _, cid = workspace
    workflow = create_workflow(client, headers, cid)
    response = decide(client, headers, workflow)
    assert response.status_code == 200, response.text
    approved = response.json()
    assert approved["status"] == "approved"
    assert approved["decision"]["definition_hash"] == approved["definition_hash"]
    assert approved["decision"]["decided_by"] == approved["created_by"]
    assert approved["decision"]["decided_at"]
    assert decide(client, headers, workflow).json() == approved
    assert decide(client, headers, workflow, "rejected").status_code == 409
    eligibility = client.get(f"{version_path(workflow)}/eligibility", headers=headers).json()
    assert eligibility["eligible"] is True
    assert eligibility["reasons"] == []
    assert eligibility["execution_available"] is False
    assert client.post(f"{workflow_path(workflow)}/runs", headers=headers).status_code in (404, 405)


def test_new_version_preserves_history_and_does_not_inherit_approval(client, workspace):
    headers, _, cid = workspace
    workflow = create_workflow(client, headers, cid)
    assert decide(client, headers, workflow).status_code == 200
    definition = copy.deepcopy(DEFINITION)
    definition["limits"]["max_steps"] = 5
    response = client.post(f"{workflow_path(workflow)}/versions", headers=headers, json={"expected_version": 1, "definition": definition})
    assert response.status_code == 201, response.text
    v2 = response.json()
    assert v2["number"] == 2 and v2["status"] == "draft" and v2["decision"] is None
    assert v2["definition_hash"] != workflow["latest_version"]["definition_hash"]
    assert client.get(workflow_path(workflow), headers=headers).json()["latest_version"] == v2
    old = client.get(version_path(workflow), headers=headers).json()
    assert old["status"] == "approved" and old["definition"] == DEFINITION
    old_gate = client.get(f"{version_path(workflow)}/eligibility", headers=headers).json()
    assert not old_gate["eligible"] and "version_superseded" in old_gate["reasons"]
    new_gate = client.get(f"{version_path(workflow, v2['id'])}/eligibility", headers=headers).json()
    assert not new_gate["eligible"] and "version_not_approved" in new_gate["reasons"]
    assert decide(client, headers, workflow).status_code == 409
    assert (
        client.post(
            f"{workflow_path(workflow)}/versions", headers=headers, json={"expected_version": 1, "definition": DEFINITION}
        ).status_code
        == 409
    )
    assert decide(client, headers, workflow, version_id=v2["id"]).status_code == 200
    assert client.get(f"{version_path(workflow, v2['id'])}/eligibility", headers=headers).json()["eligible"] is True


def test_rejection_is_final_for_that_version(client, workspace):
    headers, _, cid = workspace
    workflow = create_workflow(client, headers, cid)
    rejected = decide(client, headers, workflow, "rejected", "Needs narrower tool scope")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["decision"]["reason"] == "Needs narrower tool scope"
    assert decide(client, headers, workflow).status_code == 409
    assert client.get(f"{version_path(workflow)}/eligibility", headers=headers).json()["eligible"] is False


@pytest.mark.parametrize("role", ["analyst", "operator", "viewer"])
def test_draft_and_approval_permissions_are_separate(client, workspace, role):
    admin, firm_id, cid = workspace
    headers, _ = member(firm_id, role)
    workflow = create_workflow(client, admin, cid)
    assert client.get(workflow_path(workflow), headers=headers).status_code == 200
    response = client.post(f"/api/companies/{cid}/workflows", headers=headers, json={"name": "Another workflow", "definition": DEFINITION})
    assert response.status_code == (403 if role == "viewer" else 201)
    assert decide(client, headers, workflow).status_code == 403
    assert decide(client, headers, workflow, "rejected").status_code == 403
    assert decide(client, admin, workflow).status_code == 200
    gate = client.get(f"{version_path(workflow)}/eligibility", headers=headers).json()
    assert gate["eligible"] is (role == "operator")
    if role != "operator":
        assert "execution_permission_required" in gate["reasons"]
    if role == "viewer":
        assert (
            client.post(
                f"{workflow_path(workflow)}/versions", headers=headers, json={"expected_version": 1, "definition": DEFINITION}
            ).status_code
            == 403
        )


def test_foreign_firm_and_wrong_company_cannot_address_workflows(client, workspace):
    headers, _, cid = workspace
    foreign, _ = make_firm(role="admin")
    workflow = create_workflow(client, headers, cid)
    sibling = client.post("/api/portfolio/companies", headers=headers, json={"name": "Sibling Company"}).json()["id"]
    for path in (
        f"/api/companies/{cid}/workflows",
        workflow_path(workflow),
        f"{workflow_path(workflow)}/versions",
        version_path(workflow),
        f"{version_path(workflow)}/eligibility",
    ):
        assert client.get(path, headers=foreign).status_code == 404
        if workflow["id"] in path:
            assert client.get(path.replace(cid, sibling), headers=headers).status_code == 404
    assert decide(client, foreign, workflow).status_code == 404
    assert (
        client.post(f"/api/companies/{cid}/workflows", headers=foreign, json={"name": "Sneak", "definition": DEFINITION}).status_code == 404
    )
    assert (
        client.post(
            f"{workflow_path(workflow)}/versions", headers=foreign, json={"expected_version": 1, "definition": DEFINITION}
        ).status_code
        == 404
    )
    other_workflow = create_workflow(client, headers, cid)
    assert client.get(version_path(other_workflow, workflow["latest_version"]["id"]), headers=headers).status_code == 404
    assert client.get(f"/api/companies/{sibling}/workflows", headers=headers).json() == []


def test_authentication_and_firm_membership_are_required(client, workspace, tenant_factory):
    admin, _, cid = workspace
    workflow = create_workflow(client, admin, cid)
    plain, _, _ = tenant_factory()
    assert client.get(workflow_path(workflow)).status_code == 401
    assert client.get(workflow_path(workflow), headers=plain).status_code == 403
    assert decide(client, plain, workflow).status_code == 403


@pytest.mark.parametrize(
    "change",
    [
        {"goal": "   "},
        {"allowed_tools": []},
        {"allowed_tools": ["read_invoice", "read_invoice"]},
        {"environment": "production"},
        {"limits": {"max_steps": 0}},
        {"limits": {"max_cost_usd": "-1"}},
        {"approved": True},
    ],
)
def test_invalid_definitions_cannot_be_created(client, workspace, change):
    headers, _, cid = workspace
    definition = {**DEFINITION, **change}
    response = client.post(f"/api/companies/{cid}/workflows", headers=headers, json={"name": "Invalid workflow", "definition": definition})
    assert response.status_code == 422, response.text
    assert client.get(f"/api/companies/{cid}/workflows", headers=headers).json() == []


def test_competing_edits_do_not_overwrite_a_version(client, workspace):
    headers, _, cid = workspace
    workflow = create_workflow(client, headers, cid)

    def submit():
        return client.post(
            f"{workflow_path(workflow)}/versions", headers=headers, json={"expected_version": 1, "definition": DEFINITION}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert sorted(results) == [201, 409]
    versions = client.get(f"{workflow_path(workflow)}/versions", headers=headers).json()
    assert [v["number"] for v in versions] == [2, 1]


def test_version_and_decision_history_is_immutable_in_postgres(client, workspace):
    headers, _, cid = workspace
    workflow = create_workflow(client, headers, cid)
    assert decide(client, headers, workflow).status_code == 200
    with platform_session() as session:
        schema = session.scalar(select(Tenant.schema_name).join(FirmCompany).where(FirmCompany.id == uuid.UUID(cid)))
    for statement in (
        "UPDATE workflow_versions SET definition = '{}'::jsonb WHERE id = :id",
        "DELETE FROM workflow_versions WHERE id = :id",
        "UPDATE workflow_approvals SET decision = 'rejected' WHERE version_id = :id",
        "DELETE FROM workflow_approvals WHERE version_id = :id",
    ):
        with tenant_session(schema) as session, pytest.raises(DBAPIError, match="immutable"):
            session.execute(text(statement), {"id": uuid.UUID(workflow["latest_version"]["id"])})
    assert client.get(version_path(workflow), headers=headers).json()["definition"] == DEFINITION
    assert client.get(f"{version_path(workflow)}/eligibility", headers=headers).json()["eligible"] is True


def test_approval_race_keeps_one_final_decision(client, workspace):
    headers, _, cid = workspace
    workflow = create_workflow(client, headers, cid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda choice: decide(client, headers, workflow, choice).status_code, ("approved", "rejected")))
    assert sorted(results) == [200, 409]
    version = client.get(version_path(workflow), headers=headers).json()
    assert version["decision"]["decision"] == version["status"]


def test_permission_changes_are_reflected_by_eligibility(client, workspace):
    admin, firm_id, cid = workspace
    operator, user_id = member(firm_id, "operator")
    workflow = create_workflow(client, admin, cid)
    assert decide(client, admin, workflow).status_code == 200
    path = f"{version_path(workflow)}/eligibility"
    assert client.get(path, headers=operator).json()["eligible"] is True
    with platform_session() as session:
        membership = session.scalar(select(FirmMembership).where(FirmMembership.user_id == user_id))
        membership.role = "viewer"
        session.commit()
    assert client.get(path, headers=operator).json()["eligible"] is False


def test_history_cannot_be_updated_or_spoofed_through_the_api(client, workspace):
    headers, _, cid = workspace
    workflow = create_workflow(client, headers, cid)
    for method in (client.put, client.patch, client.delete):
        assert method(version_path(workflow), headers=headers).status_code == 405
    response = client.post(
        f"{version_path(workflow)}/decision",
        headers=headers,
        json={"decision": "approved", "decided_by": str(uuid.uuid4())},
    )
    assert response.status_code == 422
    assert client.get(version_path(workflow), headers=headers).json()["status"] == "draft"


def test_migration_preserves_existing_history_and_can_be_repeated(client, workspace):
    from vista.tenancy import migrate_tenant_schema

    headers, _, cid = workspace
    workflow = create_workflow(client, headers, cid)
    assert decide(client, headers, workflow).status_code == 200
    with platform_session() as session:
        schema = session.scalar(select(Tenant.schema_name).join(FirmCompany).where(FirmCompany.id == uuid.UUID(cid)))
    migrate_tenant_schema(schema)
    assert client.get(version_path(workflow), headers=headers).json()["status"] == "approved"
    assert client.get(f"{version_path(workflow)}/eligibility", headers=headers).json()["eligible"] is True
