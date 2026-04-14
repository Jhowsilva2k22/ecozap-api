from app.services.ai import AIService
from app.services.memory import MemoryService
from app.services.whatsapp import WhatsAppService
import logging

logger = logging.getLogger(__name__)

def build_attendant_prompt(owner: dict, customer: dict, history_summary: str) -> str:
    name = owner.get("business_name", "a empresa")
    tone = owner.get("tone", "acolhedor e direto")
    vocab = ", ".join(owner.get("vocabulary", []) or [])
    context = owner.get("context_summary", "")
    emoji_style = owner.get("emoji_style", "medio")
    faqs = owner.get("faqs") or []
    faqs_text = "\n- ".join(faqs) if faqs else "nenhuma FAQ cadastrada"
    customer_name = customer.get("name") or "o cliente"
    customer_summary = customer.get("summary") or "primeiro contato"
    return f"""Voce e o assistente de atendimento de {name}. Resolva, encante e fidelize.

PERSONA: Tom={tone} | Vocab={vocab} | Emojis={emoji_style}
NEGOCIO: {context}
CLIENTE: {customer_name} | {customer_summary}
FAQs:\n- {faqs_text}

Regras: Respostas curtas (max 4 linhas). Nunca diga que e IA.
Historico: {history_summary or 'inicio'}"""

class AttendantAgent:
    def __init__(self):
        self.ai = AIService()
        self.memory = MemoryService()
        self.whatsapp = WhatsAppService()

    async def process(self, phone: str, owner_id: str, message: str):
        customer = await self.memory.get_or_create_customer(phone, owner_id)
        owner = await self.memory.get_owner_context(owner_id)
        if not owner:
            return
        history = await self.memory.get_conversation_history(phone, owner_id)
        classification = await self.ai.classify_intent(message, context=customer.summary or "")
        is_simple = classification.get("is_simple", False)
        intent = classification.get("intent", "outros")
        await self.memory.save_turn(phone, owner_id, "user", message)
        system_prompt = build_attendant_prompt(owner=owner, customer=customer.model_dump(), history_summary=customer.summary or "")
        response = await self.ai.respond(system_prompt=system_prompt, history=history, user_message=message, use_gemini=is_simple)
        await self.memory.save_turn(phone, owner_id, "assistant", response)
        await self.memory.update_customer(phone, owner_id, {"last_intent": intent, "total_messages": (customer.total_messages or 0) + 1})
        await self.whatsapp.send_typing(phone, duration=len(response) * 40)
        await self.whatsapp.send_message(phone, response)
        logger.info(f"[Attendant] {phone} | intent={intent}")
