"""The portfolio seeder must attach the firm to the analyst account that already
holds the live key, not mint a second one."""

import runpy
import secrets
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text

from tests.conftest import requires_db
from vista.db import platform_session
from vista.models.platform import FirmMembership, Tenant, User
from vista.security import token_digest
from vista.tenancy import migrate_tenant_schema

SEEDER = Path(__file__).resolve().parents[1] / "scripts" / "seed_portfolio_demo.py"


@pytest.fixture()
def seeder():
    # Load the script as a module once so its helpers are callable in-process.
    return runpy.run_path(str(SEEDER), run_name="seed_portfolio_demo")


_CREATED_SCHEMAS: list[str] = []


@pytest.fixture(autouse=True)
def _drop_legacy_tenants():
    """Remove every tenant a test invented, so other tests that walk all
    tenants (the scheduler) never meet a row whose schema is gone."""
    yield
    with platform_session() as s:
        for schema in _CREATED_SCHEMAS:
            tenant = s.scalar(select(Tenant).where(Tenant.schema_name == schema))
            if tenant is not None:
                for u in s.scalars(select(User).where(User.tenant_id == tenant.id)).all():
                    s.execute(FirmMembership.__table__.delete().where(FirmMembership.user_id == u.id))
                    s.delete(u)
                # users.tenant_id is a bare FK with no ORM relationship, so the
                # unit of work cannot order these deletes itself.
                s.flush()
                s.delete(tenant)
            s.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        s.commit()
    _CREATED_SCHEMAS.clear()


def _existing_analyst(email: str) -> tuple[uuid.UUID, str]:
    """A pre-existing analyst in some other tenant, as production has today.

    The tenant's schema is really created: other tests walk every tenant row
    (the scheduler does), so a phantom tenant with no schema would break them.
    """
    key = secrets.token_hex(32)
    schema = f"t_{uuid.uuid4().hex[:12]}"
    with platform_session() as s:
        tenant = Tenant(name=f"legacy-{uuid.uuid4().hex[:6]}", schema_name=schema)
        s.add(tenant)
        s.flush()
        user = User(tenant_id=tenant.id, email=email, role="admin")
        user.api_token = key
        s.add(user)
        s.commit()
        user_id = user.id
    migrate_tenant_schema(schema)
    _CREATED_SCHEMAS.append(schema)
    return user_id, key


@requires_db
def test_seed_firm_reuses_the_existing_analyst_and_keeps_their_key(seeder):
    email = seeder["ANALYST"]["email"]
    with platform_session() as s:
        for u in s.scalars(select(User).where(User.email == email)).all():
            s.execute(FirmMembership.__table__.delete().where(FirmMembership.user_id == u.id))
            s.delete(u)
        s.commit()
    user_id, key = _existing_analyst(email)

    with platform_session() as s:
        firm = seeder["seed_firm"](s, None)  # no key given: keep whatever the account has
        s.commit()
        members = s.scalars(select(FirmMembership).where(FirmMembership.firm_id == firm.id)).all()
        assert [m.user_id for m in members] == [user_id], "membership attaches to the existing account"
        assert s.get(User, user_id).api_token_hash == token_digest(key), "the live key is untouched"
        assert s.scalar(select(User).where(User.email == email, User.id != user_id)) is None, "no duplicate user"


@requires_db
def test_seed_firm_finds_the_account_by_key_and_rotates_only_when_asked(seeder):
    email = seeder["ANALYST"]["email"]
    with platform_session() as s:
        for u in s.scalars(select(User).where(User.email == email)).all():
            s.execute(FirmMembership.__table__.delete().where(FirmMembership.user_id == u.id))
            s.delete(u)
        s.commit()
    user_id, key = _existing_analyst(email)
    new_key = secrets.token_hex(32)

    with platform_session() as s:
        seeder["seed_firm"](s, key)  # same key: found by hash, nothing rotated
        s.commit()
        assert s.get(User, user_id).api_token_hash == token_digest(key)
    with platform_session() as s:
        seeder["seed_firm"](s, new_key)  # unknown key: falls back to email, stamps the new key
        s.commit()
        assert s.get(User, user_id).api_token_hash == token_digest(new_key)
        assert s.scalar(select(User).where(User.email == email, User.id != user_id)) is None


@requires_db
def test_seed_firm_refuses_to_create_an_analyst_without_a_key(seeder):
    email = seeder["ANALYST"]["email"]
    with platform_session() as s:
        for u in s.scalars(select(User).where(User.email == email)).all():
            s.execute(FirmMembership.__table__.delete().where(FirmMembership.user_id == u.id))
            s.delete(u)
        s.commit()
    with platform_session() as s, pytest.raises(SystemExit):
        seeder["seed_firm"](s, None)
