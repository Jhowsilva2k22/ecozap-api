from app.services.ai import AIService
from app.services.memory import MemoryService
from app.services.whatsapp import WhatsAppService
import logging

logger = logging.getLogger(__name__)
HANDOFF_SCORE = 70

# Palavras-chave para detectar canal de origem na primeira mensagem
_CHANNEL_KEYWORDS = {
    "reels": "reels",
    "reel": "reels",
    "stories": "stories",
    "story": "stories",
    "anuncio": "anuncio",
    "anúncio": "anuncio",
    "ads": "anuncio",
    "trafego": "anuncio",
    "tráfego": "anuncio",
    "youtube": "youtube",
    "yt": "youtube",
    "video": "video",
    "vídeo": "video",
    "post": "post",
    "feed": "feed",
    "direct": "direct",
    "dm": "direct",
    "indicação": "indicacao",
    "indicacao": "indicacao",
    "indicou": "indicacao",
    "me indicaram": "indicacao",
    "amigo": "indicacao",
    "google": "google",
    "pesquisa": "google",
    "site": "site",
    "utm": "campanha",
}

def _detect_channel(message: str) -> str:
    msg_lower = message.lower()
    for keyword, channel in _CHANNEL_KEYWORDS.items():
        if keyword in msg_lower:
            return channel
    return ""

def build_qualifier_prompt(owner: dict, customer: dict, history_summary: str) -> str:
    name = owner.get("business_name", "a empresa")
    tone = owner.get("tone", "acolhedor e direto")
    values = ", ".join(owner.get("values", []) or [])
    vocab = ", ".join(owner.get("vocabulary", []) or [])
    offer = owner.get("main_offer", "nossos servicos")
    audience = owner.get("target_audience", "pessoas interessadas")
    objections = "\n- ".join(owner.get("common_objections", []) or [])
    context = owner.get("context_summary", "")
    emoji_style = owner.get("emoji_style", "medio")
    questions = owner.get("qualification_questions") or ["Voce esta buscando isso pra voce mesmo ou pra sua empresa?", "Ja tentou resolver isso antes?", "Tem disponibilidade para comecar esse mes?"]
    questions_text = "\n- ".join(questions)
    customer_name = customer.get("name") or None
    customer_summary = customer.get("summary") or "primeiro contato"
    customer_score = customer.get("lead_score", 0)
    total_msgs = customer.get("total_messages", 0)
    name_instruction = ""
    if not customer_name and total_msgs <= 2:
        name_instruction = "\nNOME: Ainda não sei o nome. Pergunte de forma natural e casual numa das primeiras trocas (apenas UMA vez)."
    display_name = customer_name or "o lead"
    return f"""Você é {name}, conversando com um lead pelo WhatsApp.

CONTEXTO DO NEGÓCIO: {context}
OFERTA: {offer} | PÚBLICO: {audience}
TOM: {tone} | VOCABULÁRIO: {vocab} | EMOJIS: {emoji_style}
VALORES: {values}
OBJEÇÕES COMUNS: {objections or 'nenhuma'}

LEAD: {display_name} | Score={customer_score}/100 | {customer_summary}{name_instruction}

━━ REGRAS DE FORMATO ━━
- Frases curtas, linguagem de WhatsApp, sem formalidade
- ZERO bullet points, ZERO listas, ZERO asteriscos
- Máximo 3 frases por mensagem
- UMA pergunta por vez, curiosa e casual
- Nunca revele que é IA, robô, sistema ou automação
- Se pedirem algo impossível (ligar, marcar fora do chat): redirecione naturalmente — "aqui pelo chat resolve mais rápido, o que você precisa?"
- Emojis só quando surgir naturalmente, nunca como hábito
- Imagem/áudio/PDF: reaja em 1-2 frases naturais + UMA pergunta

━━ INTELIGÊNCIA COMPORTAMENTAL ━━
RAPPORT E ESPELHAMENTO: Espelhe sutilmente o ritmo, energia e vocabulário do lead. Se ele é expansivo, acompanhe. Se é contido, seja mais direto. Isso cria conexão inconsciente antes de qualquer argumento (Bandler/Grinder, Chris Voss).

SISTEMA 1 (decisão emocional): As pessoas decidem emocionalmente e justificam depois com lógica (Kahneman). Conecte primeiro com a dor ou desejo — só depois traga lógica ou detalhes. Nunca abra com funcionalidades, abra com o que muda na vida delas.

ROTULAGEM EMOCIONAL: Quando sentir resistência, nomeie o sentimento em vez de rebater — ex: "parece que você já tentou algo assim antes e não funcionou como esperava..." Isso desativa a guarda e cria abertura (Chris Voss/FBI).

PERGUNTAS SPIN (use na ordem, sem forçar):
1. Situação: entenda o contexto atual sem interrogar
2. Problema: descubra a dor real ("o que mais te trava hoje nisso?")
3. Implicação: amplifique a consequência de não resolver ("e como isso tá afetando...?")
4. Necessidade: deixe o lead verbalizar o que precisa (Neil Rackham)

PROPÓSITO SOBRE PRODUTO: Pessoas compram transformação, não produto. Conecte a oferta com identidade e propósito — quem elas querem se tornar, não só o que vão receber (Viktor Frankl, James Clear). Especialmente em contexto de fé: conecte com missão de vida.

ESCASSEZ E RECIPROCIDADE LEGÍTIMAS: Ofereça algo de valor real sem pedir nada em troca primeiro (reciprocidade genuína). Escassez só quando for verdade — nunca pressão falsa. O que é genuíno converte; o que é forçado afasta (Cialdini).

ANCORAGEM: Quando falar de valor, sempre ancore alto antes de apresentar o preço real. O contraste faz o preço parecer menor — mas use isso com honestidade (Ariely).

MICRO-COMPROMETIMENTOS: Antes de pedir uma decisão grande, consiga pequenos "sins" — concordâncias, reações positivas, engajamento. Isso cria consistência psicológica (Cialdini — Comprometimento e Coerência).

OBJEÇÕES SÃO PEDIDOS DE INFORMAÇÃO: Quando o lead objeção, ele ainda está interessado — quem não quer, some. Trate cada objeção como uma pergunta disfarçada e responda com curiosidade, não com defesa. Use CNV (Rosenberg): valide antes de responder.

FÉ E VALORES: Se o contexto permitir, conecte com propósito maior — impacto, legado, família. Pessoas movidas por propósito têm comprometimento diferente. Não pregue — dialogue.

━━ PERGUNTAS DE QUALIFICAÇÃO ━━
{questions_text}

HISTÓRICO: {history_summary or 'primeiro contato'}"""

class QualifierAgent:
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
            if not media_base64:
                logger.warning(f"[Qualifier] falha ao baixar mídia tipo={media_type} id={message_id}")

        if media_type == "audio" and media_base64:
            transcription = await self.ai.transcribe_audio(media_base64)
            if transcription:
                display_message = f"[Áudio]: {transcription}"
            media_base64 = None
            media_type = "text"
        elif media_type == "audio" and not media_base64:
            display_message = "[Áudio recebido - não foi possível processar]"
        # ────────────────────────────────────────────────────────────────────

        # ── Captura de nome (primeira mensagem curta sem nome salvo) ─────────
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
        intent = classification.get("intent", "outros")
        score_delta = classification.get("lead_score_delta", 0)
        is_simple = classification.get("is_simple", False)
        new_score = min(100, max(0, (customer.lead_score or 0) + score_delta))
        handoff_threshold = owner.get("handoff_threshold", HANDOFF_SCORE)
        if new_score >= handoff_threshold and customer.lead_score < handoff_threshold:
            await self._trigger_handoff(phone, owner, customer, display_message)
        await self.memory.save_turn(phone, owner_id, "user", display_message)
        system_prompt = build_qualifier_prompt(owner=owner, customer=customer.model_dump(), history_summary=customer.summary or "")

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
        await self.memory.update_customer(phone, owner_id, {"lead_score": new_score, "last_intent": intent, "total_messages": (customer.total_messages or 0) + 1})
        await self.whatsapp.send_typing(phone, duration=len(response) * 40)
        await self.whatsapp.send_message(phone, response)
        logger.info(f"[Qualifier] {phone} | intent={intent} | score={new_score} | media={media_type}")

    async def _trigger_handoff(self, phone: str, owner: dict, customer, message: str):
        notify_phone = owner.get("notify_phone")
        if not notify_phone:
            return
        customer_name = customer.name or phone
        alert = f"*Lead Quente!*\n\n{customer_name} ({phone})\nScore: {customer.lead_score}/100\nUltima mensagem: {message}\n\nAcesse o painel para ver o historico."
        await self.whatsapp.send_message(notify_phone, alert)
