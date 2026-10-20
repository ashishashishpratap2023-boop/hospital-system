from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./dev.db"
    secret_key: str = "dev-only-change-me-dev-only-change-me"
    access_token_minutes: int = 30
    cookie_secure: bool = False
    frontend_origin: str = "http://localhost:3000"
    whatsapp_bridge_url: str = ""
    bridge_secret: str = ""
    admin_email: str = ""
    admin_password: str = ""

    @field_validator("database_url")
    @classmethod
    def _fix_pg_url(cls, v: str) -> str:
        # Render gives postgres:// or postgresql://; SQLAlchemy+psycopg3 needs postgresql+psycopg://
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v


settings = Settings()
