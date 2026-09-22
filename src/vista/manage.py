"""Operator-only provisioning: python -m vista.manage --help."""

import argparse
import json
import secrets
import uuid
from pathlib import Path

from sqlalchemy import delete, text

from vista.db import engine, platform_session, tenant_session
from vista.models.platform import BrowserSession, Tenant, User
from vista.models.tenant import Deal, DealMembership
from vista.storage import ensure_bucket
from vista.tenancy import migrate_all_tenants, migrate_platform, provision_tenant

# Arbitrary constant; serialises `migrate` across containers that start together.
MIGRATION_LOCK_ID = 7_310_552_001


def migrate() -> None:
    """Upgrade shared + tenant schemas and ensure the report bucket exists.
    Holds a Postgres advisory lock so concurrent task launches (ECS canary
    deployments, autoscaling) run migrations one at a time."""
    with engine.connect() as lock_conn:
        lock_conn.execute(text("SELECT pg_advisory_lock(:id)"), {"id": MIGRATION_LOCK_ID})
        lock_conn.commit()
        try:
            migrate_platform()
            migrate_all_tenants()
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": MIGRATION_LOCK_ID})
            lock_conn.commit()
    ensure_bucket()


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
    seed = commands.add_parser("seed-portfolio", help="seed the analyst demo firm and its companies")
    seed.add_argument("--skip-cedar", action="store_true")
    seed.add_argument("--analyst-key", default=None)
    commands.add_parser(
        "load-synthetic",
        help="load the six synthetic_data/ companies through the fact + interpretation layers",
        description="Arguments are passed through to scripts/load_synthetic_portfolio.py "
        "(--analyst-key, --replace-firm SLUG, --only, --no-analyze, --processor, --accept-model-mappings).",
    )
    args, loader_args = parser.parse_known_args()
    if loader_args and args.command != "load-synthetic":
        parser.error(f"unrecognized arguments: {' '.join(loader_args)}")
    if args.command == "migrate":
        migrate()
        return
    if args.command in ("seed-portfolio", "load-synthetic"):
        # scripts/ ships in the image; run it in-process so manage.sh, which can
        # only invoke `python -m vista.manage`, can reach it inside AWS.
        import runpy
        import sys

        if args.command == "seed-portfolio":
            name = "seed_portfolio_demo.py"
            argv = [name]
            if args.skip_cedar:
                argv.append("--skip-cedar")
            if args.analyst_key:
                argv += ["--analyst-key", args.analyst_key]
        else:
            name = "load_synthetic_portfolio.py"
            argv = [name, *loader_args]
        script = Path(__file__).resolve().parents[2] / "scripts" / name
        if not script.exists():
            parser.error(f"{name} not found at {script}; is scripts/ in the image?")
        sys.argv = argv
        runpy.run_path(str(script), run_name="__main__")
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
