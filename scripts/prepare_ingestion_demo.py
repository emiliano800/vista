"""Prepare an empty Meridian workspace on the local database for the ingestion demo.

Run: uv run python scripts/prepare_ingestion_demo.py
Then: VISTA_COOKIE_SECURE=false uv run uvicorn vista.main:app --port 8010

Credentials are reused from a gitignored file. This command refuses remote
Postgres/S3 endpoints and never alters the deployed Meridian workspace.
"""

import json
import os
import uuid
from pathlib import Path
from urllib.parse import urlparse

from vista.auth import principal_for_key
from vista.config import settings
from vista.db import engine, tenant_session
from vista.models.tenant import Deal, DealMembership
from vista.storage import ensure_bucket
from vista.tenancy import migrate_platform, migrate_tenant_schema, provision_tenant

ROOT = Path(__file__).resolve().parents[1]
CREDENTIALS = ROOT / "Vista" / "ingestion-demo.json"
ACCESS = ROOT / "Vista" / "INGESTION_DEMO_ACCESS.md"


def prepare():
    local = {"localhost", "127.0.0.1", "::1"}
    if engine.url.host not in local or urlparse(settings.s3_endpoint_url or "").hostname not in local:
        raise SystemExit("This demo setup only runs with local Postgres and local MinIO.")
    migrate_platform()
    ensure_bucket()
    CREDENTIALS.parent.mkdir(parents=True, exist_ok=True)
    if CREDENTIALS.exists():
        info = json.loads(CREDENTIALS.read_text())
        principal = principal_for_key(info["access_key"])
        migrate_tenant_schema(principal.tenant_schema)
        with tenant_session(principal.tenant_schema) as session:
            company = session.get(Deal, uuid.UUID(info["company_id"]))
            if company is None or company.name != "Meridian Risk Partners, LLC":
                raise SystemExit("Saved demo credentials do not match the expected workspace.")
    else:
        tenant, user, token = provision_tenant("Vista Capital · Local ingestion demo", "demo@meridianrisk.com")
        with tenant_session(tenant.schema_name) as session:
            company = Deal(name="Meridian Risk Partners, LLC", created_by=user.id)
            session.add(company)
            session.flush()
            session.add(DealMembership(deal_id=company.id, user_id=user.id, role="owner"))
            session.commit()
            info = {"tenant_id": str(tenant.id), "company_id": str(company.id), "access_key": token}
        with open(CREDENTIALS, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as fp:
            json.dump(info, fp, indent=2)
    ACCESS.write_text(
        "# Local Meridian ingestion demo\n\n"
        "Open http://localhost:8010/signin/ and use this local demo access key:\n\n"
        f"```\n{info['access_key']}\n```\n\n"
        "Choose **Try Meridian sample data → Review field mapping → Confirm & analyze**.\n\n"
        "The five files contain 246 synthetic records. Open the commission finding to see "
        "the $1,584.48 discrepancy and its two supporting source rows.\n\n"
        "This is a separate local workspace. Production credentials and data are unchanged.\n"
    )
    ACCESS.chmod(0o600)
    print(f"Local demo ready. Sign-in instructions: {ACCESS}")


if __name__ == "__main__":
    prepare()
