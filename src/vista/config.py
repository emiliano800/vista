from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VISTA_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://vista:vista@localhost:5432/vista"
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "vista"
    s3_secret_key: str = "vista-secret"
    s3_bucket: str = "vista-documents"


settings = Settings()
