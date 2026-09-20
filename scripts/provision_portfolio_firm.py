"""Provision the PE analyst firm: one tenant whose deals are the portfolio.

    uv run python scripts/provision_portfolio_firm.py --name "Northstar HVAC Holdings" \
        --analyst sarah@northstarhvac.com

The firm is the tenant; each portfolio company is a deal inside it, so every
existing per-deal surface (imports, findings, agents, runs) already scopes
correctly. Prints the analyst access key once — it is never stored in clear.
"""

import argparse
from datetime import date

from sqlalchemy import select

from vista.db import platform_session, tenant_session
from vista.models.platform import Tenant
from vista.models.tenant import Deal, PortfolioActivity
from vista.tenancy import migrate_platform, provision_tenant

# The six synthetic back-office companies that already have data in the repo.
# The profile is descriptive context for the workspace header and the
# integration checklist; it never feeds a calculated figure.
PORTFOLIO_COMPANIES = [
    ("Meridian Risk Partners, LLC", "Insurance broking", "Hartford, CT"),
    ("Harborline Insurance Brokers, Inc.", "Insurance broking", "Providence, RI"),
    ("Castlebrook Agency", "Insurance broking", "Albany, NY"),
    ("Northfield Industrial Components, Inc.", "Industrial goods", "Akron, OH"),
    ("Keystone Bearing & Drive Co.", "Industrial goods", "Erie, PA"),
    ("Ridgeway Fasteners & Supply", "Industrial goods", "Toledo, OH"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="Northstar HVAC Holdings", help="the acquiring firm")
    parser.add_argument("--analyst", default="sarah@northstarhvac.com", help="analyst login email")
    parser.add_argument("--acquired", default=date.today().isoformat(), help="acquisition date recorded on each company")
    args = parser.parse_args()

    migrate_platform()

    with platform_session() as session:
        existing = session.scalars(select(Tenant).where(Tenant.name == args.name)).first()
        if existing is not None:
            raise SystemExit(f"Firm {args.name!r} already exists (schema {existing.schema_name}). Delete it first or pick another --name.")

    tenant, owner, token = provision_tenant(args.name, args.analyst)
    print(f"Firm tenant : {tenant.name} (schema {tenant.schema_name})")
    print(f"Analyst     : {owner.email} (role {owner.role})")

    with tenant_session(tenant.schema_name) as session:
        for name, industry, location in PORTFOLIO_COMPANIES:
            deal = Deal(
                name=name,
                created_by=owner.id,
                profile={"industry": industry, "location": location, "acquired": args.acquired},
            )
            session.add(deal)
            session.flush()
            session.add(
                PortfolioActivity(
                    deal_id=deal.id,
                    kind="import",
                    summary=f"{name} added to the {tenant.name} portfolio.",
                    actor=owner.email,
                    ref={"deal_id": str(deal.id)},
                )
            )
            print(f"  company   : {name} -> deal {deal.id}")
        session.commit()

    print("\nAnalyst access key (shown once — put it in DEMO_ACCESS.md or a password manager):")
    print(f"  {token}")
    print("\nSign in at /signin/analyst/ with that key.")


if __name__ == "__main__":
    main()
