from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    supabase_url: str
    supabase_anon_key: str
    supabase_service_key: str
    anthropic_api_key: str
    google_api_key: str = ""
    evolution_api_url: str
    evolution_api_key: str
    evolution_instance: str
    redis_url: str = "redis://localhost:6379/0"
    firecrawl_api_key: str = ""
    app_secret: str = "secret"
    app_url: str = "http://localhost:8000"
    debug: bool = False

    class Config:
        env_file = ".env"
        case_sensitive = False

@lru_cache()
def get_settings() -> Settings:
    return Settings()
