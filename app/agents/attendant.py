from app.services.ai import AIService
from app.services.memory import MemoryService
from app.services.whatsapp import WhatsAppService
from app.services import sender
from app.agents.qualifier import _detect_channel
import logging
import re
import json as _json

logger = logging.getLogger(__name__)

# ── Agendamento: Redis state machine ─────────────────────────────────────────
try:
    import redis as _redis_lib
    from app.config import get_settings as _get_cfg
    _sched_redis = _redis_lib.from_url(_get_cfg().redis_url, decode_responses=True)
except Exception:
    _sched_redis = None
_SCHED_TTL = 1800  # 30 min

def build_attendant_prompt(owner: dict, customer: dict, history_summary: str) -> str:
    name = owner.get("business_name", "a empresa")
    tone = owner.get("tone", "acolhedor e direto")
    vocab = ", ".join(owner.get("vocabulary", []) or [])
    context = owner.get("context_summary", "")
    emoji_style = owner.get("emoji_style", "medio")
    faqs = owner.get("faqs") or []
    faqs_text = "\n- ".join(faqs) if faqs else "nenhuma FAQ cadastrada"
    raw_name = customer.get("name") or ""
    customer_name = raw_name or "o cliente"
    customer_summary = customer.get("summary") or "primeiro contato"
    name_usage = f"\nUSO DO NOME: O nome do cliente é {raw_name}. Use com naturalidade — não em toda mensagem, mas nos momentos certos (acolhimento, pergunta importante, virada emocional). Nunca use de forma mecânica ou repetitiva." if raw_name else ""
    return f"""Você é {name}, atendendo pelo Instagram Direct (ou WhatsApp).

NEGÓCIO: {context}
TOM: {tone} | VOCABULÁRIO: {vocab} | EMOJIS: {emoji_style}
CLIENTE: {customer_name} | {customer_summary}{name_usage}
FAQs: {faqs_text}

━━ REGRAS DE FORMATO ━━
- Frases curtas, linguagem de conversa real — como alguém que entende e se importa
- ZERO bullet points, ZERO listas, ZERO asteriscos, ZERO formalidade
- Máximo 4 frases por mensagem — direto ao ponto
- Nunca revele que é IA, robô, sistema ou automação
- Nunca comece com "Olá!", "Oi!", "Claro!" ou "Com certeza!" — entre direto no assunto
- Varie o início das mensagens — nunca repita a mesma abertura duas vezes seguidas
- Se pedirem algo impossível (ligar, marcar fora do chat): redirecione — "aqui pelo chat resolve mais rápido, o que você precisa?"
- Imagem/áudio/PDF: reaja natural em 1-2 frases
- Máximo 1 emoji por mensagem, e só quando fizer sentido emocional. Sem emoji é sempre melhor do que emoji forçado
- NUNCA use "mano", "cara", "kkk", "kkkk" — isso soa jovem demais e pouco profissional
- NUNCA tente ser engraçado ou fazer piada — humor pode surgir, mas nunca forçado
- Espelhamento SEGUE energia positiva — nunca espelhe agressividade ou grosseria
- Quando o cliente vier alterado: fique centrado. Valide o sentimento sem validar o tom. Use o que sabe sobre comportamento humano para amenizar — por trás da raiva quase sempre há frustração não resolvida. Não entre na pilha, não seja frio, não pregue. Firme e humano, sempre
- A paz que você carrega na resposta é mais poderosa do que qualquer argumento. Quando você acolhe alguém que veio bravo com genuinidade e calor, muitos vão naturalmente se acalmar e até pedir desculpas por conta própria. Não force isso — apenas segure o espaço. A resposta certa sempre será acolhimento e tratamento adequado, independente de como a pessoa chegou

━━ LEITURA HUMANA NO ATENDIMENTO ━━
Sua função vai além de resolver — é estar presente com quem está do outro lado. Clientes trazem perguntas, mas muitas vezes carregam mais do que isso.

ESCUTA REAL: Leia o que está por trás da mensagem. "Quero cancelar" pode ser frustração acumulada, não decisão final. "Tá demorando" pode ser ansiedade, não impaciência. Entenda antes de responder.

VALIDE ANTES DE RESOLVER: Reconheça o sentimento ou situação antes de dar a solução — "faz sentido você estar frustrado com isso" abre mais do que ir direto à resposta. A pessoa precisa se sentir ouvida primeiro.

USE O NOME: Quando souber o nome, use. Com naturalidade, não como script. Isso cria pertencimento real.

SINAIS DE ALGO MAIOR: Se a pessoa trouxer algo além do atendimento — um desabafo, uma pressão, uma situação difícil — reconheça com humanidade. Não ignore, não minimize, não exagere. Esteja presente.

DÊ SENSO DE PROGRESSO: Quando há etapas ou espera, mostre avanço — "já está encaminhado", "o próximo passo é..." Isso reduz ansiedade e gera confiança.

ENTREGUE MAIS DO QUE FOI PEDIDO: Quando fizer sentido, traga uma dica, uma observação útil, algo além do mínimo. Não por obrigação — por cuidado genuíno.

REENCADRE PROBLEMAS: Quando algo deu errado, vá direto para a solução com calma — "entendo, vamos resolver assim..." Transforma frustração em confiança sem drama.

CLAREZA ACIMA DE TUDO: Respostas simples e diretas resolvem mais e geram menos atrito. Não complique o que pode ser resolvido com honestidade e objetividade.

PROFISSIONALISMO COM CALOR: Você pode ser humano e próximo sem perder o fio do atendimento. Cuidado e profissionalismo não se excluem — se complementam.

OPT-OUT COM DIGNIDADE: Se o cliente pedir para parar de receber mensagens, não insista. Peça desculpas com educação e calor — "desculpa qualquer incômodo, de verdade", despeça-se humanamente e deixe claro que quando ele quiser voltar, é só chamar. Sem drama, sem culpa, sem tentativa de reter. Respeite a decisão.

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

        # Canal do lead (whatsapp ou instagram)
        ch = customer.channel or "whatsapp"

        # ── Boas-vindas no primeiro contato ─────────────────────────────────
        is_first_contact = (customer.total_messages or 0) == 0
        welcome_msg = (owner.get("welcome_message") or "")
        if is_first_contact and welcome_msg:
            final_welcome = welcome_msg.replace("{nome}", customer.name or "")
            final_welcome = final_welcome.replace("{negocio}", owner.get("business_name", ""))
            await sender.send_typing(phone, channel=ch, duration=len(final_welcome) * 40)
            await sender.send_message(phone, final_welcome, channel=ch)
            await self.memory.save_turn(phone, owner_id, "assistant", final_welcome)

        history = await self.memory.get_conversation_history(phone, owner_id)

        # ── Processa mídia (mantém fluxo de texto intacto) ──────────────────
        display_message = message
        media_base64 = None

        if media_type in ("image", "audio", "document") and message_id:
            media_base64 = await sender.download_media(message_id, phone=phone, channel=ch)

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

        # ── Detecção de opt-out de nurturing ───────────────────────────────
        if _detect_nurture_optout(display_message):
            await self.memory.update_customer(phone, owner_id, {"nurture_paused": True})
            logger.info(f"[Attendant] {phone} pediu opt-out de nurturing")

        # ── Detecção de aniversário ─────────────────────────────────────────
        if not customer.birthday:
            detected_bday = _detect_birthday(display_message)
            if detected_bday:
                await self.memory.update_customer(phone, owner_id, {"birthday": detected_bday})
                logger.info(f"[Attendant] Aniversário detectado para {phone}: {detected_bday}")

        # ── Agendamento: verifica estado de fluxo em andamento ──────────────
        sched_state = _sched_state_get(phone, owner_id)
        if sched_state:
            sched_response = await _sched_handle_step(
                phone, owner_id, display_message, owner, customer, sched_state
            )
            if sched_response:
                await self.memory.save_turn(phone, owner_id, "user", display_message)
                await self.memory.save_turn(phone, owner_id, "assistant", sched_response)
                await self.memory.update_customer(phone, owner_id, {
                    "total_messages": (customer.total_messages or 0) + 1,
                })
                await sender.send_typing(phone, channel=ch, duration=len(sched_response) * 40)
                await sender.send_message(phone, sched_response, channel=ch)
                return

        classification = await self.ai.classify_intent(display_message, context=customer.summary or "")
        logger.info(f"[Attendant] {phone} classify_intent raw: {classification}")
        is_simple = classification.get("is_simple", False)
        intent = classification.get("intent", "outros")
        score_delta = classification.get("lead_score_delta", 0)
        old_score = customer.lead_score or 0
        new_score = min(100, max(0, old_score + score_delta))
        logger.info(f"[Attendant] {phone} score: {old_score} + {score_delta} = {new_score}")

        # ── Progressão automática de status baseada no score ────────────
        new_status = _auto_status(customer.lead_status, new_score)

        # ── Detecção automática de venda confirmada ─────────────────────
        if intent == "compra_confirmada" and new_status != "cliente":
            new_status = "cliente"
            new_score = 100
            # Notifica o dono
            notify_phone = owner.get("notify_phone")
            if notify_phone:
                clean_phone = re.sub(r'\D', '', phone)
                name = customer.name or "Sem nome"
                channel = customer.channel or "não identificado"
                alert = (
                    f"💰 *Venda Detectada!*\n\n"
                    f"👤 *{name}*\n"
                    f"📱 wa.me/{clean_phone}\n"
                    f"📍 Canal: {channel}\n\n"
                    f"Status atualizado pra *cliente* automaticamente."
                )
                await self.whatsapp.send_message(notify_phone, alert)
            logger.info(f"[Attendant] VENDA DETECTADA! {phone} virou cliente automaticamente")

        # ── SOS: Escalonamento inteligente ──────────────────────────────
        needs_human = classification.get("needs_human", False)
        human_reason = classification.get("human_reason", "")
        sentiment = classification.get("sentiment", "neutro")
        sos_sent = False

        if needs_human and customer.lead_status != "em_atendimento_humano":
            notify_phone = owner.get("notify_phone")
            if notify_phone:
                clean_phone = re.sub(r'\D', '', phone)
                name = customer.name or "Sem nome"
                urgency = classification.get("urgency", "media")
                urgency_icon = "🔴" if urgency == "alta" else "🟡"

                sos_alert = (
                    f"{urgency_icon} *SOS — Atenção necessária!*\n\n"
                    f"👤 *{name}* | Score: *{new_score}*\n"
                    f"📱 wa.me/{clean_phone}\n"
                    f"🎭 Sentimento: *{sentiment}*\n"
                    f"📌 Motivo: {human_reason}\n\n"
                )
                if customer.summary:
                    sos_alert += f"📝 Contexto: {customer.summary[:200]}\n\n"
                sos_alert += "👉 Copie a próxima mensagem e envie pra assumir:"
                await self.whatsapp.send_message(notify_phone, sos_alert)
                # Mensagem separada SÓ com o comando — long press = copia tudo
                await self.whatsapp.send_message(notify_phone, f"/assumir {phone}")
                sos_sent = True
                logger.info(f"[SOS] Alerta enviado para dono! {phone} | motivo: {human_reason}")

        # ── Agendamento: inicia fluxo se intent for agendamento e Google conectado ─
        if intent == "agendamento" and owner.get("google_access_token") and not sched_state:
            sched_response = await _sched_start_flow(phone, owner_id, owner, customer)
            if sched_response:
                await self.memory.save_turn(phone, owner_id, "user", display_message)
                await self.memory.save_turn(phone, owner_id, "assistant", sched_response)
                await self.memory.update_customer(phone, owner_id, {
                    "lead_score": new_score, "lead_status": new_status,
                    "last_intent": intent,
                    "total_messages": (customer.total_messages or 0) + 1,
                    "last_sentiment": sentiment,
                })
                await sender.send_typing(phone, channel=ch, duration=len(sched_response) * 40)
                await sender.send_message(phone, sched_response, channel=ch)
                return

        # ── Gera resposta ───────────────────────────────────────────────
        await self.memory.save_turn(phone, owner_id, "user", display_message)

        # Se SOS foi acionado, injeta instrução de fallback no prompt
        sos_instruction = ""
        if sos_sent:
            sos_instruction = (
                "\n\n━━ ATENÇÃO: MODO CONTENÇÃO ━━\n"
                "O dono foi notificado e vai assumir em breve. "
                "NÃO invente respostas, NÃO prometa nada, NÃO dê informações que você não tem certeza. "
                "Segure a conversa com naturalidade: reconheça o que o cliente disse, "
                "valide o sentimento, e diga que vai verificar/confirmar e já retorna. "
                "Exemplo: 'Entendi perfeitamente. Deixa eu verificar isso com mais cuidado pra te dar a melhor resposta. Já te retorno!'"
            )

        system_prompt = build_attendant_prompt(
            owner=owner, customer=customer.model_dump(),
            history_summary=customer.summary or ""
        ) + sos_instruction

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
        sent_history = list(customer.sentiment_history or [])[-9:]
        sent_history.append(sentiment)
        await self.memory.update_customer(phone, owner_id, {
            "lead_score": new_score, "lead_status": new_status,
            "last_intent": intent, "total_messages": (customer.total_messages or 0) + 1,
            "last_sentiment": sentiment, "sentiment_history": sent_history
        })
        await sender.send_typing(phone, channel=ch, duration=len(response) * 40)
        await sender.send_message(phone, response, channel=ch)
        logger.info(f"[Attendant] {phone} | intent={intent} | score={new_score} | status={new_status} | sos={sos_sent} | media={media_type} | ch={ch}")


def _auto_status(current_status: str, score: int) -> str:
    """Progressão automática de status baseada no score.
    Nunca regride: cliente > quente > morno > qualificando > novo.
    Status 'em_atendimento_humano' e 'perdido' são manuais, não muda."""
    if current_status in ("em_atendimento_humano", "perdido", "cliente"):
        return current_status
    if score >= 70:
        return "quente"
    if score >= 40:
        return "morno"
    if score >= 15:
        return "qualificando"
    return current_status  # mantém "novo" se score ainda baixo


# ── Helpers de detecção ──────────────────────────────────────────────────────

_OPTOUT_PATTERNS = [
    r"para[r]?\s*(de\s*)?(mandar|enviar)\s*(mensage[mn]s?|msg)",
    r"n[aã]o\s*(me\s*)?(mand[ae]|envi[ae])\s*(mais\s*)?(mensage[mn]s?|msg)",
    r"n[aã]o\s*quero\s*(mais\s*)?(receber|mensage[mn])",
    r"para\s*com\s*(as\s*)?(mensage[mn]s?|msg)",
    r"me\s*tir[ae]\s*(d[aeo]s?\s*)?(lista|mensage[mn])",
    r"chega\s*de\s*mensage[mn]",
    r"n[aã]o\s*precis[ao]\s*(mais\s*)?de\s*(vocês|vcs|contato)",
    r"cancelar?\s*(mensage[mn]s?|contato|envio)",
]

def _detect_nurture_optout(message: str) -> bool:
    """Detecta se o cliente está pedindo pra parar de receber mensagens."""
    msg_lower = message.lower().strip()
    for pattern in _OPTOUT_PATTERNS:
        if re.search(pattern, msg_lower):
            return True
    return False


def _detect_birthday(message: str) -> str:
    """Detecta data de aniversário mencionada em mensagem.
    Retorna 'DD/MM' se encontrar, string vazia se não."""
    msg_lower = message.lower()

    # Padrões: "meu aniversário é 15/03", "nasci dia 15 de março", "faço aniversário 15/03"
    # DD/MM ou DD/MM/AAAA
    date_match = re.search(
        r'(?:anivers[aá]rio|nasci|fa[çc]o\s*anos?|niver)\s*(?:[eé:]?\s*)?(?:dia\s*)?(\d{1,2})[/\-](\d{1,2})',
        msg_lower
    )
    if date_match:
        day, month = date_match.group(1), date_match.group(2)
        if 1 <= int(day) <= 31 and 1 <= int(month) <= 12:
            return f"{int(day):02d}/{int(month):02d}"

    # "nasci dia 15 de março", "meu niver é 3 de janeiro"
    _MONTHS = {
        "janeiro": "01", "fevereiro": "02", "março": "03", "marco": "03",
        "abril": "04", "maio": "05", "junho": "06", "julho": "07",
        "agosto": "08", "setembro": "09", "outubro": "10",
        "novembro": "11", "dezembro": "12"
    }
    text_match = re.search(
        r'(?:anivers[aá]rio|nasci|fa[çc]o\s*anos?|niver)\s*(?:[eé:]?\s*)?(?:dia\s*)?(\d{1,2})\s*(?:de\s*)(\w+)',
        msg_lower
    )
    if text_match:
        day = text_match.group(1)
        month_text = text_match.group(2)
        month = _MONTHS.get(month_text, "")
        if month and 1 <= int(day) <= 31:
            return f"{int(day):02d}/{month}"

    return ""


# ── Agendamento: state machine helpers ────────────────────────────────────────

def _sched_state_get(phone: str, owner_id: str) -> dict:
    if not _sched_redis:
        return {}
    try:
        raw = _sched_redis.get(f"sched:{phone}:{owner_id}")
        return _json.loads(raw) if raw else {}
    except Exception:
        return {}


def _sched_state_set(phone: str, owner_id: str, state: dict):
    if not _sched_redis:
        return
    try:
        _sched_redis.setex(f"sched:{phone}:{owner_id}", _SCHED_TTL, _json.dumps(state))
    except Exception:
        pass


def _sched_state_clear(phone: str, owner_id: str):
    if not _sched_redis:
        return
    try:
        _sched_redis.delete(f"sched:{phone}:{owner_id}")
    except Exception:
        pass


def _parse_slot_choice(text: str, num_slots: int) -> int:
    t = text.strip().lower()
    for i in range(1, num_slots + 1):
        if t in (str(i), f"opção {i}", f"opcao {i}", f"slot {i}"):
            return i - 1
    written = {"um": 0, "uma": 0, "dois": 1, "duas": 1, "três": 2, "tres": 2, "quatro": 3, "cinco": 4}
    if t in written and written[t] < num_slots:
        return written[t]
    return -1


async def _sched_start_flow(phone: str, owner_id: str, owner: dict, customer) -> str:
    from app.services.calendar import GoogleCalendarService
    from app.config import get_settings
    cfg = get_settings()
    svc = GoogleCalendarService(
        access_token=owner.get("google_access_token", ""),
        refresh_token=owner.get("google_refresh_token", ""),
        client_id=cfg.google_client_id,
        client_secret=cfg.google_client_secret,
    )
    calendar_id = owner.get("google_calendar_id") or "primary"
    try:
        slots = await svc.get_free_slots(calendar_id=calendar_id, days_ahead=5, slot_duration_min=60, max_slots=5)
    except Exception as e:
        logger.error(f"[Sched] Erro ao buscar slots: {e}")
        return "Tive um probleminha ao verificar a agenda. Pode tentar de novo em instantes?"
    if not slots:
        return "Não encontrei horários disponíveis nos próximos dias. Me avisa e verifico outra janela."
    lines = ["Ótimo! Aqui estão os horários disponíveis:\n"]
    for i, s in enumerate(slots, 1):
        lines.append(f"{i}. {s['label']}")
    lines.append("\nQual prefere? Responda com o número.")
    _sched_state_set(phone, owner_id, {"step": "offering_slots", "slots": slots})
    return "\n".join(lines)


async def _sched_handle_step(phone: str, owner_id: str, message: str,
                              owner: dict, customer, state: dict) -> str:
    from app.config import get_settings
    cfg = get_settings()
    step = state.get("step")

    if step == "offering_slots":
        slots = state.get("slots", [])
        idx = _parse_slot_choice(message, len(slots))
        if idx < 0:
            lines = ["Não entendi. Responda com o número do horário:\n"]
            for i, s in enumerate(slots, 1):
                lines.append(f"{i}. {s['label']}")
            return "\n".join(lines)
        chosen = slots[idx]
        customer_email = getattr(customer, "email", None) or ""
        if not customer_email:
            _sched_state_set(phone, owner_id, {"step": "awaiting_email", "chosen_slot": chosen})
            return f"Perfeito! Horário escolhido: *{chosen['label']}*\n\nPreciso do seu e-mail para enviar a confirmação. Qual é?"
        return await _sched_create_and_confirm(phone, owner_id, owner, customer, chosen, customer_email, cfg)

    elif step == "awaiting_email":
        import re as _re
        email_match = _re.search(r"[\w._%+\-]+@[\w.\-]+\.[a-z]{2,}", message.lower())
        if not email_match:
            return "Não reconheci um e-mail válido. Pode enviar no formato nome@exemplo.com?"
        customer_email = email_match.group(0)
        from app.services.memory import MemoryService
        mem = MemoryService()
        await mem.update_customer(phone, owner_id, {"email": customer_email})
        chosen = state.get("chosen_slot", {})
        return await _sched_create_and_confirm(phone, owner_id, owner, customer, chosen, customer_email, cfg)

    elif step == "awaiting_confirmation":
        _confirm = ["sim", "confirmei", "recebi", "ok", "certo", "perfeito", "tá bom", "ta bom", "confirmado", "s", "👍"]
        if any(w in message.lower().strip() for w in _confirm):
            slot_label = state.get("slot_label", "")
            name = getattr(customer, "name", None) or "você"
            _sched_state_clear(phone, owner_id)
            return f"Ótimo, {name}! Até {slot_label}. Se precisar de algo antes, é só chamar!"
        meet_link = state.get("meet_link", "")
        slot_label = state.get("slot_label", "")
        return f"Confirma que recebeu o e-mail com os detalhes da reunião?\n\n📅 *{slot_label}*\n🔗 {meet_link}"

    _sched_state_clear(phone, owner_id)
    return ""


async def _sched_create_and_confirm(phone, owner_id, owner, customer, chosen, customer_email, cfg) -> str:
    from app.services.calendar import GoogleCalendarService
    svc = GoogleCalendarService(
        access_token=owner.get("google_access_token", ""),
        refresh_token=owner.get("google_refresh_token", ""),
        client_id=cfg.google_client_id,
        client_secret=cfg.google_client_secret,
    )
    business_name = owner.get("business_name", "a empresa")
    customer_name = getattr(customer, "name", None) or "Cliente"
    calendar_id = owner.get("google_calendar_id") or "primary"
    try:
        event = await svc.create_event_with_meet(
            calendar_id=calendar_id,
            title=f"Reunião — {customer_name}",
            start_iso=chosen["start"],
            end_iso=chosen["end"],
            attendee_email=customer_email,
            description=f"Agendado via EcoZap | WhatsApp: {phone}",
        )
        meet_link = event.get("meet_link", "")
        slot_label = chosen.get("label", "horário confirmado")
        await svc.send_confirmation_email(
            to_email=customer_email,
            customer_name=customer_name,
            business_name=business_name,
            slot_label=slot_label,
            meet_link=meet_link,
        )
        _sched_state_set(phone, owner_id, {"step": "awaiting_confirmation", "slot_label": slot_label, "meet_link": meet_link})
        return (
            f"Reunião confirmada! ✅\n\n"
            f"📅 *{slot_label}*\n🔗 {meet_link}\n\n"
            f"Enviei confirmação para *{customer_email}*. Você recebeu?"
        )
    except Exception as e:
        logger.error(f"[Sched] Erro ao criar evento: {e}")
        _sched_state_clear(phone, owner_id)
        return "Tive um problema ao criar o evento. Pode tentar novamente em instantes."
