from pydantic import BaseModel
from typing import Optional


class CustomerProfile(BaseModel):
    """Customer profile model."""
    id: str
    owner_id: str
    whatsapp: str
    name: str
    email: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True
