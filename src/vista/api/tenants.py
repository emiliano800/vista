import secrets

from fastapi import APIRouter, Header, HTTPException

from vista.api.schemas import TenantCreate, TenantCreated
from vista.config import settings
from vista.tenancy import provision_tenant

router = APIRouter(tags=["tenants"])


@router.post("/tenants", response_model=TenantCreated, status_code=201)
def create_tenant(body: TenantCreate, x_vista_provisioning_key: str = Header(default="")) -> TenantCreated:
    if not settings.provisioning_key or not secrets.compare_digest(x_vista_provisioning_key, settings.provisioning_key):
        raise HTTPException(403, "Tenant creation is restricted to operators")
    tenant, owner, token = provision_tenant(body.name, body.owner_email)
    return TenantCreated(
        tenant_id=tenant.id,
        name=tenant.name,
        owner_user_id=owner.id,
        owner_email=owner.email,
        api_token=token,
    )
