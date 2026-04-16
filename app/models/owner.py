from pydantic import BaseModel
from typing import Optional


class OwnerProfile(BaseModel):
    """Owner profile model."""
    id: str
    name: str
    whatsapp: str
    email: Optional[str] = None
    google_access_token: Optional[str] = None
    google_refresh_token: Optional[str] = None
    google_calendar_id: str = "primary"
    google_email: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True
