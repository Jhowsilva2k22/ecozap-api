from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class IncomingMessage(BaseModel):
    instance: str
    phone: str
    message: str
    message_id: str
    timestamp: datetime = None
    is_from_me: bool = False
    media_type: Optional[str] = None

class OutgoingMessage(BaseModel):
    phone: str
    message: str
    instance: str

class ConversationTurn(BaseModel):
    role: str
    content: str
    timestamp: datetime = None
    intent_detected: Optional[str] = None
    lead_score_delta: Optional[int] = None
