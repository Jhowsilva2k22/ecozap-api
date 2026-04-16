from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application configuration."""
    database_url: str
    supabase_url: str
    supabase_key: str
    supabase_service_key: str
    whatsapp_api_url: str
    whatsapp_api_token: str
    whatsapp_phone_number_id: str
    redis_url: str = "redis://localhost:6379"
    google_client_id: str = ""
    google_client_secret: str = ""

    class Config:
        env_file = ".env"
