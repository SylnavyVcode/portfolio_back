from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Supabase
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str
    supabase_jwt_secret: str = ""  # vide => vérification via JWKS

    # Application
    environment: str = "development"
    frontend_url: str = "http://localhost:5173"
    cors_origins: str = "http://localhost:5173"

    # Emailing (module 5)
    brevo_api_key: str = ""
    email_sender_name: str = "Valmy Mabika"
    email_sender_address: str = ""

    # Paiements
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    flutterwave_secret_key: str = ""
    flutterwave_webhook_hash: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
