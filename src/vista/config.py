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
    s3_bucket: str = "vista-reports"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    # Any OpenAI-compatible endpoint, e.g. https://openrouter.ai/api/v1
    openai_base_url: str | None = None
    # OpenRouter provider pinning: comma-separated ("nvidia" → only that provider, no fallbacks)
    openai_provider_only: str = ""
    # TypeSafe Jev (System One): typed judgments over code-supplied candidates, via agents/jev.py.
    # Unset → stub answers (no / none / 0), so nothing is proposed without a model.
    typesafe_api_key: str | None = None
    typesafe_model: str = "jev-latest"
    typesafe_base_url: str = "https://api.typesafe.ai/v1"
    # Which model interprets a recorder submission: "jev" judges workflow candidates code derived
    # from the observed facts; "chat" is the prose-JSON interpretation by the OpenAI-compatible model.
    recorder_interpreter: str = "jev"  # jev|chat

    def openai_client(self):
        from openai import OpenAI

        return OpenAI(api_key=self.openai_api_key, base_url=self.openai_base_url)

    def openai_completion_kwargs(self, max_tokens: int, temperature: float | None = None) -> dict:
        """Per-model completion arguments. Reasoning models (gpt-5+, o-series)
        reject `max_tokens` and explicit temperature, and need headroom for
        hidden reasoning tokens on top of the visible answer."""
        reasoning = self.openai_model.rsplit("/", 1)[-1].startswith(("gpt-5", "gpt-6", "o1", "o3", "o4"))
        kwargs: dict = {"max_completion_tokens": max(max_tokens, 8192) if reasoning else max_tokens}
        if temperature is not None and not reasoning:
            kwargs["temperature"] = temperature
        return kwargs

    def openai_extra_body(self) -> dict:
        body: dict = {}
        if self.openai_base_url and "openrouter.ai" in self.openai_base_url:
            body["reasoning"] = {"enabled": False}  # answers only; no thinking tokens
        only = [p.strip() for p in self.openai_provider_only.split(",") if p.strip()]
        if only:
            body["provider"] = {"only": only, "allow_fallbacks": False}
        return body

    # PE portfolio workspace. Synthetic mode pins "today" so the seeded reporting
    # period stays stable; the import processor flag selects the ImportProcessor
    # implementation behind the /import-jobs API (demo = deterministic parser).
    use_synthetic_data: bool = True
    demo_today: str = "2026-03-31"
    import_processor: str = "demo"  # demo|agent
    enable_agent_import: bool = False
    enable_portfolio_agent: bool = False
    # Optional fixed access key for the seeded demo analyst (scripts/seed_portfolio_demo.py).
    demo_analyst_key: str | None = None

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
