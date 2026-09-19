"""Settings composition for hosted environments (no database needed)."""

from vista.config import Settings


def _settings(**env):
    # _env_file=None: ignore the developer's local .env so the test is deterministic.
    return Settings(_env_file=None, **env)


def test_explicit_database_url_is_kept():
    s = _settings(database_url="postgresql+psycopg://u:p@db:5432/x")
    assert s.database_url == "postgresql+psycopg://u:p@db:5432/x"


def test_database_url_composed_from_parts_with_escaping():
    s = _settings(db_host="vista.abc.us-east-1.rds.amazonaws.com", db_user="vista", db_password="p@ss:w/rd", db_sslmode="require")
    assert s.database_url == "postgresql+psycopg://vista:p%40ss%3Aw%2Frd@vista.abc.us-east-1.rds.amazonaws.com:5432/vista?sslmode=require"


def test_empty_strings_mean_unset():
    s = _settings(s3_endpoint_url="", s3_access_key="", s3_secret_key="", s3_region="", db_host="", db_password="")
    assert s.s3_endpoint_url is None and s.s3_access_key is None and s.s3_secret_key is None
    assert s.s3_region is None and s.db_host is None
    # Without db_host the database_url default is untouched.
    assert s.database_url.startswith("postgresql+psycopg://vista:vista@localhost")


def test_s3_client_uses_iam_role_when_no_endpoint(monkeypatch):
    import vista.storage as storage

    captured = {}

    def fake_client(service, **kwargs):
        captured["service"] = service
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(storage.boto3, "client", fake_client)
    monkeypatch.setattr(storage, "settings", _settings(s3_endpoint_url="", s3_access_key="", s3_secret_key="", s3_region="us-east-2"))
    storage.s3_client()
    assert captured == {"service": "s3", "region_name": "us-east-2"}

    captured.clear()
    monkeypatch.setattr(storage, "settings", _settings(s3_endpoint_url="http://localhost:9000", s3_access_key="a", s3_secret_key="b"))
    storage.s3_client()
    assert captured == {"service": "s3", "endpoint_url": "http://localhost:9000", "aws_access_key_id": "a", "aws_secret_access_key": "b"}
