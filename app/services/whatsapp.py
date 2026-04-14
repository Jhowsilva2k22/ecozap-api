import httpx
from app.config import get_settings
from app.models.message import IncomingMessage
import logging

logger = logging.getLogger(__name__)

class WhatsAppService:
    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.evolution_api_url.rstrip("/")
        self.headers = {"apikey": self.settings.evolution_api_key, "Content-Type": "application/json"}

    async def send_message(self, phone: str, text: str) -> dict:
        url = f"{self.base_url}/message/sendText/{self.settings.evolution_instance}"
        phone = self._format_phone(phone)
        payload = {"number": phone, "text": text, "delay": 1200}
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                response = await client.post(url, json=payload, headers=self.headers)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(f"Erro ao enviar mensagem para {phone}: {e}")
                raise

    async def send_typing(self, phone: str, duration: int = 3000):
        url = f"{self.base_url}/chat/sendPresence/{self.settings.evolution_instance}"
        phone = self._format_phone(phone)
        payload = {"number": phone, "options": {"presence": "composing", "delay": duration}}
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                await client.post(url, json=payload, headers=self.headers)
            except Exception:
                pass

    def parse_webhook(self, payload: dict):
        try:
            event = payload.get("event", "")
            if event != "messages.upsert":
                return None
            data = payload.get("data", {})
            key = data.get("key", {})
            if key.get("fromMe", False):
                return None
            message_data = data.get("message", {})
            conversation = (message_data.get("conversation") or message_data.get("extendedTextMessage", {}).get("text") or "")
            if not conversation:
                return None
            phone = key.get("remoteJid", "").replace("@s.whatsapp.net", "")
            return IncomingMessage(instance=payload.get("instance", ""), phone=phone, message=conversation, message_id=key.get("id", ""))
        except Exception as e:
            logger.error(f"Erro ao parsear webhook: {e}")
            return None

    def _format_phone(self, phone: str) -> str:
        phone = phone.replace("+", "").replace("-", "").replace(" ", "")
        if not phone.endswith("@s.whatsapp.net"):
            phone = f"{phone}@s.whatsapp.net"
        return phone
