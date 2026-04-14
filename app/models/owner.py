from pydantic import BaseModel
from typing import Optional

class OwnerProfile(BaseModel):
    id: str
    phone: str
    business_name: str
    business_type: Optional[str] = None
    tone: Optional[str] = None
    vocabulary: Optional[list] = None
    emoji_style: Optional[str] = None
    avg_response_length: Optional[str] = None
    values: Optional[list] = None
    product_description: Optional[str] = None
    main_offer: Optional[str] = None
    price_range: Optional[str] = None
    target_audience: Optional[str] = None
    common_objections: Optional[list] = None
    faqs: Optional[list] = None
    links_processed: Optional[list] = None
    context_summary: Optional[str] = None
    agent_mode: str = "qualifier"
    qualification_questions: Optional[list] = None
    handoff_threshold: int = 70
    notify_phone: Optional[str] = None
    notify_on_hot_lead: bool = True
    daily_summary_time: str = "20:00"
