from fastapi import APIRouter, Request, HTTPException
from app.services.whatsapp import WhatsAppService
from app.services.memory import MemoryService
from app.queues.tasks import process_message, learn_from_links
import logging
import re

logger = logging.getLogger(__name__)
router = APIRouter()
whatsapp = WhatsAppService()
memory = MemoryService()

# Prefixos que o DONO usa para ensinar o bot
LEARN_PREFIXES = ("aprender:", "aprender ", "configurar:", "configurar ", "link:", "base:")

@router.post("/webhook/whatsapp")
async def receive_whatsapp(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Payload invalido")

    message = whatsapp.parse_webhook(payload)
    if not message:
        return {"status": "ignored"}

    owner = await _get_owner_by_instance(message.instance)
    if not owner:
        return {"status": "owner_not_found"}

    # ── Comando de aprendizado (só quando o dono manda) ──────────────────────
    owner_phone = _normalize_phone(owner.get("phone", ""))
    sender_phone = _normalize_phone(message.phone)

    if sender_phone == owner_phone and message.media_type == "text":
        msg_lower = message.message.lower().strip()
        if any(msg_lower.startswith(p) for p in LEARN_PREFIXES):
            links = _extract_urls(message.message)
            if links:
                learn_from_links.apply_async(
                    args=[owner["id"], links],
                    queue="learning"
                )
                await whatsapp.send_message(
                    message.phone,
                    f"📚 Recebi {len(links)} link(s)! Vou processar e aprender. Pode levar até 2 minutos."
                )
                return {"status": "learning_queued"}

    # ── Fluxo normal de atendimento ──────────────────────────────────────────
    process_message.apply_async(
        args=[message.phone, owner["id"], message.message, owner.get("agent_mode", "both"),
              message.message_id, message.media_type or "text"],
        queue="messages",
        routing_key=f"phone.{message.phone}"
    )
    return {"status": "queued"}


@router.get("/webhook/health")
async def health():
    return {"status": "ok", "service": "whatsapp-agent"}


async def _get_owner_by_instance(instance: str):
    db = memory.db
    result = db.table("owners").select("*").eq("evolution_instance", instance).maybe_single().execute()
    return result.data if result and result.data else None


def _normalize_phone(phone: str) -> str:
    return re.sub(r'\D', '', phone or "")


def _extract_urls(text: str) -> list:
    pattern = r'https?://[^\s]+'
    return list(set(re.findall(pattern, text)))
