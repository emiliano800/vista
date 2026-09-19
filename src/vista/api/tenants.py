from fastapi import APIRouter

from vista.api.schemas import TenantCreate, TenantCreated
from vista.tenancy import provision_tenant

router = APIRouter(tags=["tenants"])


@router.post("/tenants", response_model=TenantCreated, status_code=201)
def create_tenant(body: TenantCreate) -> TenantCreated:
    # NOTE: unauthenticated for milestone 1; gate behind an ops/admin token
    # before any real deployment.
    tenant, owner, token = provision_tenant(body.name, body.owner_email)
    return TenantCreated(
        tenant_id=tenant.id,
        name=tenant.name,
        owner_user_id=owner.id,
        owner_email=owner.email,
        api_token=token,
    )
