from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VISTA_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://vista:vista@localhost:5432/vista"
    # Alternative to database_url for hosts that inject credentials separately
    # (for example an RDS-managed secret on ECS). When db_host is set, database_url
    # is composed from these parts and db_password is URL-escaped.
    db_host: str | None = None
    db_port: int = 5432
    db_name: str = "vista"
    db_user: str = "vista"
    db_password: str | None = None
    db_sslmode: str | None = None  # e.g. "require" on RDS

    # Leave endpoint/keys empty on AWS to use the task's IAM role and the regional S3 endpoint.
    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_access_key: str | None = "vista"
    s3_secret_key: str | None = "vista-secret"
    s3_region: str | None = None
    s3_bucket: str = "vista-documents"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    provisioning_key: str | None = None  # /tenants disabled unless configured
    cookie_secure: bool = True
    allowed_origins: list[str] = []
    session_hours: int = 8

    @model_validator(mode="after")
    def _compose(self) -> "Settings":
        # Empty strings from the environment mean "unset" for optional values.
        for name in ("s3_endpoint_url", "s3_access_key", "s3_secret_key", "s3_region", "db_host", "db_password", "db_sslmode"):
            if getattr(self, name) == "":
                setattr(self, name, None)
        if self.db_host:
            auth = quote_plus(self.db_user)
            if self.db_password is not None:
                auth += ":" + quote_plus(self.db_password)
            url = f"postgresql+psycopg://{auth}@{self.db_host}:{self.db_port}/{self.db_name}"
            if self.db_sslmode:
                url += f"?sslmode={self.db_sslmode}"
            self.database_url = url
        return self


settings = Settings()
