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


settings = Settings()
