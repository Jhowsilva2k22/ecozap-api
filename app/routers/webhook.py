from fastapi import APIRouter, Request
from app.config import Settings
from app.services.calendar import GoogleCalendarService
from app.agents.attendant import AttendantAgent
from app.models.owner import OwnerProfile
from app.models.customer import CustomerProfile

router = APIRouter()

GOOGLE_CMDS = ("/conectar_google", "/google")
PANEL_CMDS = ("/menu", "/painel")


@router.post("/webhook")
async def webhook(request: Request, settings: Settings):
    """Handle WhatsApp webhook messages."""
    data = await request.json()
    message_data = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})
    messages = message_data.get("messages", [])

    if not messages:
        return {"status": "ok"}

    message = messages[0]
    sender_id = message["from"]
    text = message["text"]["body"]

    # Get owner and customer from database
    owner = OwnerProfile(id="owner_123", name="Owner", whatsapp="5511999999999")
    customer = CustomerProfile(id="cust_123", owner_id="owner_123", whatsapp=sender_id, name="Customer")

    # Handle Google Calendar commands
    if text.lower() in GOOGLE_CMDS:
        calendar_service = GoogleCalendarService(settings.google_client_id, settings.google_client_secret)
        oauth_url = calendar_service.build_oauth_url(
            f"{settings.whatsapp_api_url}/auth/google/callback",
            owner.id
        )
        # Send OAuth URL to user via WhatsApp
        return {"status": "ok", "message": "Check your message for the authorization link."}

    # Handle panel commands
    if text.lower() in PANEL_CMDS:
        menu_text = """Menu Principal:
/conectar_google — conectar Google Calendar e Gmail
/agendar — agendar uma reunião
/help — ver mais opções"""
        return {"status": "ok", "message": menu_text}

    # Process message with attendant agent
    calendar_service = GoogleCalendarService(settings.google_client_id, settings.google_client_secret)
    agent = AttendantAgent(settings.redis_url, calendar_service, owner, customer)
    response = await agent.process(text)

    return {"status": "ok", "response": response}


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}
