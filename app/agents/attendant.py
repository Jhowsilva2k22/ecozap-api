from app.services.ai import AIService
from app.services.memory import MemoryService
from app.services.whatsapp import WhatsAppService
from app.agents.qualifier import _detect_channel
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
    return f"""Você é {name}, atendendo pelo WhatsApp.

NEGÓCIO: {context}
TOM: {tone} | VOCABULÁRIO: {vocab} | EMOJIS: {emoji_style}
CLIENTE: {customer_name} | {customer_summary}
FAQs: {faqs_text}

━━ REGRAS DE FORMATO ━━
- Frases curtas, WhatsApp natural, zero formalidade
- ZERO bullet points, ZERO listas, ZERO asteriscos
- Máximo 4 frases por mensagem
- Nunca revele que é IA, robô, sistema ou automação
- Se pedirem algo impossível (ligar, marcar fora do chat): redirecione — "aqui pelo chat resolve mais rápido, o que você precisa?"
- Imagem/áudio/PDF: reaja natural em 1-2 frases
- Emojis só quando surgir naturalmente, nunca como hábito

━━ INTELIGÊNCIA NO ATENDIMENTO ━━
ESCUTA ATIVA: Antes de responder, entenda o que está por trás da pergunta. Clientes raramente dizem a real necessidade de primeira — ouça o que não foi dito (Marshall Rosenberg, NVC).

VALIDAÇÃO ANTES DE SOLUÇÃO: Sempre valide o sentimento ou situação antes de resolver — "faz total sentido, deixa eu te ajudar com isso" cria mais confiança do que ir direto à resposta (reduz a resistência emocional).

LINGUAGEM DE IDENTIDADE: Use o nome do cliente quando souber. Trate como alguém especial, não como um ticket. "Você" importa mais do que "o cliente" — isso ativa pertencimento (James Clear — identidade).

EFEITO DE PROGRESSO: Se o cliente está num processo (espera, etapas), dê senso de avanço — "já está encaminhado", "próximo passo é..." — isso reduz ansiedade e aumenta satisfação (psicologia do progresso).

RECIPROCIDADE REAL: Entregue mais do que foi pedido quando fizer sentido — uma dica extra, uma observação útil. Isso gera lealdade genuína sem custo (Cialdini).

REENCADRAMENTO DE PROBLEMAS: Quando algo deu errado, não minimize e não dramatize — reencadre para a solução: "entendo, vamos resolver assim..." Isso transforma frustração em confiança.

DECISÕES DO SISTEMA 1: Respostas simples e diretas são processadas mais rápido e geram mais satisfação. Não complique o que pode ser resolvido com clareza e brevidade (Kahneman).

HISTÓRICO: {history_summary or 'primeiro contato'}"""

class AttendantAgent:
    def __init__(self):
        self.ai = AIService()
        self.memory = MemoryService()
        self.whatsapp = WhatsAppService()

    async def process(self, phone: str, owner_id: str, message: str,
                      message_id: str = "", media_type: str = "text"):
        customer = await self.memory.get_or_create_customer(phone, owner_id)
        owner = await self.memory.get_owner_context(owner_id)
        if not owner:
            return
        history = await self.memory.get_conversation_history(phone, owner_id)

        # ── Processa mídia (mantém fluxo de texto intacto) ──────────────────
        display_message = message
        media_base64 = None

        if media_type in ("image", "audio", "document") and message_id:
            media_base64 = await self.whatsapp.download_media_base64(message_id, phone=phone)

        if media_type == "audio" and media_base64:
            transcription = await self.ai.transcribe_audio(media_base64)
            if transcription:
                display_message = f"[Áudio]: {transcription}"
            media_base64 = None
            media_type = "text"
        # ────────────────────────────────────────────────────────────────────

        # ── Captura de nome ──────────────────────────────────────────────────
        if not customer.name:
            detected_name = await self.memory.detect_and_save_name(phone, owner_id, display_message)
            if detected_name:
                customer = await self.memory.get_or_create_customer(phone, owner_id)

        # ── Detecção de canal de origem (primeira mensagem) ──────────────────
        if not customer.channel and (customer.total_messages or 0) == 0:
            channel = _detect_channel(display_message)
            if channel:
                await self.memory.set_channel(phone, owner_id, channel)

        classification = await self.ai.classify_intent(display_message, context=customer.summary or "")
        is_simple = classification.get("is_simple", False)
        intent = classification.get("intent", "outros")
        await self.memory.save_turn(phone, owner_id, "user", display_message)
        system_prompt = build_attendant_prompt(owner=owner, customer=customer.model_dump(), history_summary=customer.summary or "")

        if media_type == "image" and media_base64:
            response = await self.ai.respond_with_image(
                system_prompt=system_prompt, history=history,
                user_message=message, image_base64=media_base64)
        elif media_type == "document" and media_base64:
            response = await self.ai.respond_with_pdf(
                system_prompt=system_prompt, history=history,
                user_message=message, pdf_base64=media_base64)
        else:
            response = await self.ai.respond(
                system_prompt=system_prompt, history=history,
                user_message=display_message, use_gemini=is_simple)

        await self.memory.save_turn(phone, owner_id, "assistant", response)
        await self.memory.update_customer(phone, owner_id, {"last_intent": intent, "total_messages": (customer.total_messages or 0) + 1})
        await self.whatsapp.send_typing(phone, duration=len(response) * 40)
        await self.whatsapp.send_message(phone, response)
        logger.info(f"[Attendant] {phone} | intent={intent} | media={media_type}")
