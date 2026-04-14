from supabase import create_client, Client
from app.config import get_settings
from functools import lru_cache

@lru_cache()
def get_db() -> Client:
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_service_key)
