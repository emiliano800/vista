"""Operator-only provisioning: python -m vista.manage --help."""

import argparse
import json
import secrets
import uuid

from sqlalchemy import delete

from vista.db import platform_session, tenant_session
from vista.models.platform import BrowserSession, Tenant, User
from vista.models.tenant import Deal, DealMembership
from vista.storage import ensure_bucket
from vista.tenancy import migrate_all_tenants, migrate_platform, provision_tenant


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    new = commands.add_parser("create-workspace")
    new.add_argument("--firm", required=True)
    new.add_argument("--company", required=True)
    new.add_argument("--email", required=True)
    add = commands.add_parser("add-user")
    add.add_argument("--tenant", required=True, type=uuid.UUID)
    add.add_argument("--company", required=True, type=uuid.UUID)
    add.add_argument("--email", required=True)
    add.add_argument("--role", choices=["member", "viewer"], default="member")
    rotate = commands.add_parser("rotate-key")
    rotate.add_argument("--user", required=True, type=uuid.UUID)
    args = parser.parse_args()
    if args.command == "migrate":
        migrate_platform()
        migrate_all_tenants()
        ensure_bucket()
        return
    if args.command == "create-workspace":
        tenant, user, token = provision_tenant(args.firm, args.email)
        with tenant_session(tenant.schema_name) as session:
            company = Deal(name=args.company, created_by=user.id)
            session.add(company)
            session.flush()
            session.add(DealMembership(deal_id=company.id, user_id=user.id, role="owner"))
            session.commit()
        company_id = company.id
    elif args.command == "add-user":
        with platform_session() as session:
            tenant = session.get(Tenant, args.tenant)
            if tenant is None:
                parser.error("Workspace not found")
            with tenant_session(tenant.schema_name) as data:
                if data.get(Deal, args.company) is None:
                    parser.error("Company not found in this workspace")
            token = secrets.token_hex(32)
            user = User(tenant_id=tenant.id, email=args.email, api_token=token, role="member")
            session.add(user)
            session.commit()
        with tenant_session(tenant.schema_name) as data:
            data.add(DealMembership(deal_id=args.company, user_id=user.id, role=args.role))
            data.commit()
        company_id = args.company
    else:
        with platform_session() as session:
            user = session.get(User, args.user)
            if user is None:
                parser.error("User not found")
            token = secrets.token_hex(32)
            user.api_token = token
            session.execute(delete(BrowserSession).where(BrowserSession.user_id == user.id))
            session.commit()
        print(json.dumps({"user_id": str(user.id), "access_key": token}, indent=2))
        return
    print(
        json.dumps(
            {"tenant_id": str(tenant.id), "company_id": str(company_id), "user_id": str(user.id), "email": user.email, "access_key": token},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
