from celery import Celery
from celery.schedules import crontab
from app.config import get_settings
from urllib.parse import quote
import asyncio
import logging
import os
from functools import wraps

logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Sentry init pro worker Celery
# ---------------------------------------------------------------------------
SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()
if SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=os.getenv("APP_ENV", "production"),
            traces_sample_rate=0.1,
            profiles_sample_rate=0.0,
            send_default_pii=False,
            integrations=[CeleryIntegration()],
        )
    except ImportError:
        logger.warning("[Sentry] sentry-sdk não instalado no worker")


# ---------------------------------------------------------------------------
# Decorator de alerta ops
# ---------------------------------------------------------------------------
from app.services.alerts import notify_error  # noqa: E402


def with_ops_alert(context_name: str):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                from app.services.ops import is_circuit_open
                if is_circuit_open(context_name):
                    logger.warning("[Ops] Circuit aberto para %s — pulando", context_name)
                    return None
            except Exception:
                pass
            try:
                result = fn(*args, **kwargs)
                try:
                    from app.services.ops import track_success
                    track_success(context_name)
                except Exception:
                    pass
                return result
            except Exception as e:
                try:
                    from app.services.ops import track_error
                    track_error(context_name, e)
                except Exception:
                    pass
                try:
                    notify_error(f"celery.{context_name}", e)
                except Exception:
                    pass
                raise
        return wrapper
    return decorator


def _panel_url() -> str:
    base = settings.app_url.rstrip("/")
    token = quote(settings.app_secret, safe="")
    return f"{base}/panel?token={token}"


celery_app = Celery("whatsapp_agent", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json", accept_content=["json"], result_serializer="json",
    timezone="America/Sao_Paulo", enable_utc=True,
    task_acks_late=True, worker_prefetch_multiplier=1,
    task_routes={
        "app.queues.tasks.process_message": {"queue": "messages"},
        "app.queues.tasks.process_buffered": {"queue": "messages"},
        "app.queues.tasks.follow_up_active": {"queue": "messages"},
        "app.queues.tasks.follow_up_cold_leads": {"queue": "messages"},
        "app.queues.tasks.nurture_customers": {"queue": "messages"},
        "app.queues.tasks.weekly_report": {"queue": "learning"},
        "app.queues.tasks.recalculate_scores": {"queue": "learning"},
        "app.queues.tasks.nightly_learning": {"queue": "learning"},
        "app.queues.tasks.nightly_learning_all": {"queue": "learning"},
        "app.queues.tasks.learn_from_links": {"queue": "learning"},
        "app.queues.tasks.run_campaign": {"queue": "learning"},
        "app.queues.tasks.daily_backup": {"queue": "learning"},
        "app.queues.tasks.health_check": {"queue": "learning"},
        "app.queues.tasks.daily_ops_report": {"queue": "learning"},
        "app.queues.tasks.daily_web_search": {"queue": "learning"},
    },
    beat_schedule={
        "nightly-learning-all": {
            "task": "app.queues.tasks.nightly_learning_all",
            "schedule": crontab(hour=3, minute=0),
            "options": {"queue": "learning"},
        },
        "daily-web-search": {
            "task": "app.queues.tasks.daily_web_search",
            "schedule": crontab(hour=6, minute=0),
            "options": {"queue": "learning"},
        },
        "follow-up-cold-leads": {
            "task": "app.queues.tasks.follow_up_cold_leads",
            "schedule": 3600.0,
            "options": {"queue": "messages"},
        },
        "nurture-customers": {
            "task": "app.queues.tasks.nurture_customers",
            "schedule": crontab(hour="8,20", minute=0),
            "options": {"queue": "messages"},
        },
        "weekly-report": {
            "task": "app.queues.tasks.weekly_report",
            "schedule": crontab(hour=8, minute=0, day_of_week=1),
            "options": {"queue": "learning"},
        },
        "daily-backup": {
            "task": "app.queues.tasks.daily_backup",
            "schedule": crontab(hour="0,6,12,18", minute=0),
            "options": {"queue": "learning"},
        },
        "health-check": {
            "task": "app.queues.tasks.health_check",
            "schedule": 1800.0,
            "options": {"queue": "learning"},
        },
        "daily-ops-report": {
            "task": "app.queues.tasks.daily_ops_report",
            "schedule": crontab(hour="1,7,13,19", minute=0),
            "options": {"queue": "learning"},
        },
        "sentinel-monitor": {
            "task": "app.queues.tasks.sentinel_monitor",
            "schedule": 300.0,
            "options": {"queue": "learning"},
        },
    },
)


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ═══════════════════════════════════════════════════════════════════════════
#  TASKS DE MENSAGEM
# ═══════════════════════════════════════════════════════════════════════════

@celery_app.task(bind=True, max_retries=3, default_retry_delay=5, queue="messages")
@with_ops_alert("process_message")
def process_message(self, phone: str, owner_id: str, message: str, agent_mode: str,
                    message_id: str = "", media_type: str = "text"):
    try:
        kwargs = {"message_id": message_id, "media_type": media_type}
        run_async(_dispatch_to_agent(phone, owner_id, message, agent_mode, **kwargs))
    except Exception as exc:
        logger.error(f"Erro ao processar mensagem de {phone}: {exc}", exc_info=True)
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5, queue="messages")
@with_ops_alert("process_buffered")
def process_buffered(self, phone: str, owner_id: str, agent_mode: str):
    """Processa mensagens agrupadas do buffer Redis (rate limiting)."""
    import json as _json
    import redis

    try:
        _redis = redis.from_url(settings.redis_url, decode_responses=True)
        buffer_key = f"buffer:{phone}:{owner_id}"
        task_key = f"buffer_task:{phone}:{owner_id}"

        raw_msgs = _redis.lrange(buffer_key, 0, -1)
        _redis.delete(buffer_key)
        _redis.delete(task_key)

        if not raw_msgs:
            logger.info(f"[Buffer] Nenhuma mensagem no buffer para {phone}")
            return

        msgs = [_json.loads(m) for m in raw_msgs]
        logger.info(f"[Buffer] Processando {len(msgs)} mensagem(ns) agrupadas de {phone}")

        media_msgs = [m for m in msgs if m.get("media_type", "text") != "text" and m.get("message_id")]
        text_parts = [m["text"] for m in msgs if m.get("text")]

        if not media_msgs:
            combined_text = "\n".join(text_parts) if text_parts else ""
            if not combined_text:
                logger.info(f"[Buffer] Mensagens vazias de {phone}, ignorando")
                return
            kwargs = {"message_id": msgs[-1].get("message_id", ""), "media_type": "text"}
            run_async(_dispatch_to_agent(phone, owner_id, combined_text, agent_mode, **kwargs))
            return

        if len(media_msgs) == 1 and len(text_parts) <= 1:
            m = media_msgs[0]
            msg_text = text_parts[0] if text_parts else (m.get("text") or "")
            kwargs = {"message_id": m.get("message_id", ""), "media_type": m.get("media_type", "text")}
            run_async(_dispatch_to_agent(phone, owner_id, msg_text, agent_mode, **kwargs))
            return

        from app.services.whatsapp import WhatsAppService
        from app.services.ai import AIService
        wa = WhatsAppService()
        ai = AIService()
        descriptions = []

        for i, media in enumerate(media_msgs, 1):
            mid = media.get("message_id", "")
            mtype = media.get("media_type", "")
            try:
                b64 = run_async(wa.download_media_base64(mid, phone=phone))
                if not b64:
                    descriptions.append(f"[Mídia {i}: não foi possível baixar]")
                    continue
                if mtype == "image":
                    desc = run_async(ai.respond_with_image(
                        system_prompt="Descreva esta imagem em 1-2 frases objetivas: o que é, marca, detalhes visíveis. Só a descrição, sem comentários.",
                        history=[], user_message="", image_base64=b64
                    ))
                    descriptions.append(f"[Imagem {i}]: {desc}")
                elif mtype == "audio":
                    text = run_async(ai.transcribe_audio(b64))
                    descriptions.append(f"[Áudio {i}]: {text}" if text else f"[Áudio {i}: não transcrito]")
                elif mtype == "document":
                    descriptions.append(f"[Documento {i}]: {media.get('text', 'documento anexado')}")
                else:
                    descriptions.append(f"[Mídia {i} ({mtype})]: anexada")
            except Exception as e:
                logger.error(f"[Buffer] Erro ao pré-analisar mídia {i} de {phone}: {e}")
                descriptions.append(f"[Mídia {i}: erro ao processar]")

        combined_text = "\n".join(text_parts + descriptions) if (text_parts or descriptions) else ""
        if not combined_text:
            logger.info(f"[Buffer] Sem conteúdo processável de {phone}")
            return

        last_msg = msgs[-1]
        kwargs = {"message_id": last_msg.get("message_id", ""), "media_type": "text"}
        run_async(_dispatch_to_agent(phone, owner_id, combined_text, agent_mode, **kwargs))

    except Exception as exc:
        logger.error(f"Erro ao processar buffer de {phone}: {exc}", exc_info=True)
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30, queue="messages")
@with_ops_alert("follow_up_active")
def follow_up_active(self, phone: str, owner_id: str):
    """Follow-up de conversa ativa — cliente silenciou no meio de uma troca."""
    try:
        import redis as _redis_lib
        import time as _time
        from app.database import get_db
        from app.services.whatsapp import WhatsAppService

        r = _redis_lib.from_url(settings.redis_url, decode_responses=True)

        ts_key = f"last_lead_msg:{phone}:{owner_id}"
        fu_key = f"followup_sent:{phone}:{owner_id}"

        already_sent = r.get(fu_key)
        if already_sent:
            logger.info(f"[Follow-up Active] Já enviado recentemente para {phone} — pulando")
            return

        last_ts = r.get(ts_key)
        if last_ts:
            elapsed = _time.time() - float(last_ts)
            if elapsed < 240:
                logger.info(f"[Follow-up Active] Lead {phone} respondeu há {elapsed:.0f}s — pulando")
                return

        db = get_db()

        customer_resp = db.table("customers").select(
            "name, lead_status, lead_score, summary"
        ).eq("phone", phone).eq("owner_id", owner_id).limit(1).execute()

        if not customer_resp.data:
            logger.info(f"[Follow-up Active] Lead {phone} não encontrado — pulando")
            return

        customer = customer_resp.data[0]

        if customer.get("lead_status") == "em_atendimento_humano":
            logger.info(f"[Follow-up Active] {phone} em atendimento humano — pulando")
            return

        owner_resp = db.table("tenants").select(
            "evolution_instance, business_name, context_summary, bot_tone"
        ).eq("id", owner_id).limit(1).execute()

        if not owner_resp.data:
            logger.info(f"[Follow-up Active] Tenant {owner_id} não encontrado — pulando")
            return

        owner = owner_resp.data[0]
        evolution_instance = owner.get("evolution_instance", "")

        if not evolution_instance:
            logger.warning(f"[Follow-up Active] Tenant {owner_id} sem evolution_instance — pulando")
            return

        history = []
        try:
            msgs_resp = db.table("messages").select(
                "role, content"
            ).eq("phone", phone).eq("owner_id", owner_id).order(
                "created_at", desc=True
            ).limit(6).execute()
            if msgs_resp.data:
                history = list(reversed(msgs_resp.data))
        except Exception as e:
            logger.warning(f"[Follow-up Active] Erro ao buscar histórico de {phone}: {e}")

        msg = _generate_active_followup(customer, owner, history)

        wa_svc = WhatsAppService()
        run_async(wa_svc.send_message(phone, msg, instance=evolution_instance))

        r.setex(fu_key, 3600, "1")
        logger.info(f"[Follow-up Active] Enviado para {phone}: '{msg[:60]}'")

    except Exception as exc:
        logger.error(f"[Follow-up Active] Erro para {phone}: {exc}")
        raise self.retry(exc=exc)


def _generate_active_followup(customer: dict, owner: dict, history: list) -> str:
    """Gera mensagem de follow-up contextual para conversa ativa."""
    try:
        from app.services.ai import AIService

        summary = customer.get("summary") or ""
        context_summary = owner.get("context_summary") or ""
        bot_tone = owner.get("bot_tone") or "amigável e direto"

        context_lines = []
        for m in history[-4:]:
            role = "Cliente" if m.get("role") == "user" else "Atendente"
            content = (m.get("content") or "")[:120].replace("\n", " ")
            context_lines.append(f"{role}: {content}")
        context_text = "\n".join(context_lines)

        if not context_text and not summary:
            return "ainda posso te ajudar com algo? 😊"

        system = (
            f"Você é um atendente de WhatsApp com tom {bot_tone}. "
            "O cliente ficou em silêncio no meio de uma conversa. "
            "Gere UMA mensagem curta (máximo 2 frases) para retomar o contato de forma natural. "
            "Use o contexto: se foi discutido algo específico, pergunte sobre aquilo. "
            "Exemplos: 'ficou alguma dúvida?', 'ainda está por aí? 😊', "
            "'posso te ajudar com mais alguma coisa?'. "
            "NÃO use o nome do cliente. NÃO invente informações. "
            "NÃO comece com saudação. Responda APENAS a mensagem pronta."
        )

        parts = []
        if context_text:
            parts.append(f"Trecho da conversa:\n{context_text}")
        if summary:
            parts.append(f"Resumo do cliente: {summary[:200]}")
        if context_summary:
            parts.append(f"Contexto do negócio: {context_summary[:150]}")
        parts.append("Gere a mensagem de follow-up:")

        ai = AIService()
        result = run_async(ai.respond(
            system_prompt=system,
            history=[],
            user_message="\n\n".join(parts)
        ))

        if result and len(result.strip()) > 3:
            return result.strip()

    except Exception as e:
        logger.warning(f"[Follow-up Active] IA falhou: {e}")

    return "ainda posso te ajudar com algo? 😊"


# -------------------------------------------------------------------------
# CONFIGURAÇÃO DOS ESTÁGIOS DE FOLLOW-UP FRIO
# -------------------------------------------------------------------------
_COLD_STAGES = {
    0: {"contact_days": 3,  "nurture_cooldown_days": 3,  "final": False},
    1: {"contact_days": 7,  "nurture_cooldown_days": 4,  "final": False},
    2: {"contact_days": 15, "nurture_cooldown_days": 7,  "final": True},
}

_COLD_FALLBACKS = {
    0: "oi! vi que conversamos há alguns dias — ficou com alguma dúvida? 😊",
    1: "ainda posso te ajudar? tenho disponibilidade pra conversar quando quiser.",
    2: "vou deixar nossa conversa em aberto — é só me chamar quando quiser 😊",
}


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5, queue="messages")
@with_ops_alert("follow_up_cold_leads")
def follow_up_cold_leads(self):
    """Follow-up de leads frios em 3 estágios progressivos."""
    try:
        from app.database import get_db
        from app.services.whatsapp import WhatsAppService
        from app.services.ai import AIService
        from app.services.ops import save_progress, get_progress, clear_progress
        from datetime import datetime, timedelta, timezone

        db = get_db()
        wa_svc = WhatsAppService()
        ai_svc = AIService()
        # Usa timezone-aware UTC para comparar corretamente com timestamps do Supabase
        now = datetime.now(timezone.utc)

        progress = get_progress("follow_up_cold_leads")
        processed_owners = set(progress.get("done", [])) if progress else set()

        owners_resp = db.table("tenants").select(
            "id, owner_phone, evolution_instance, business_name, context_summary, bot_tone"
        ).execute()
        owners = []
        for row in (owners_resp.data or []):
            r = dict(row)
            r.setdefault("phone", r.get("owner_phone", ""))
            owners.append(r)

        if not owners:
            logger.info("[Follow-up Cold] Nenhum tenant encontrado")
            return

        for owner in owners:
            owner_id = owner["id"]
            if owner_id in processed_owners:
                continue

            evolution_instance = owner.get("evolution_instance", "")
            if not evolution_instance:
                logger.warning(f"[Follow-up Cold] Tenant {owner_id} sem evolution_instance — pulando")
                processed_owners.add(owner_id)
                save_progress("follow_up_cold_leads", {"done": list(processed_owners)})
                continue

            try:
                sent_count = 0

                for stage, cfg in _COLD_STAGES.items():
                    contact_threshold = now - timedelta(days=cfg["contact_days"])
                    nurture_cooldown = now - timedelta(days=cfg["nurture_cooldown_days"])

                    resp = db.table("customers").select(
                        "phone, name, summary, follow_up_stage, last_nurture, lead_status"
                    ).eq("owner_id", owner_id).eq(
                        "follow_up_stage", stage
                    ).eq(
                        "nurture_paused", False
                    ).lt(
                        "last_contact", contact_threshold.isoformat()
                    ).neq(
                        "lead_status", "cliente"
                    ).neq(
                        "lead_status", "em_atendimento_humano"
                    ).limit(20).execute()

                    leads = resp.data or []

                    # Filtra leads onde last_nurture é recente demais
                    leads_eligible = []
                    for lead in leads:
                        last_nurture = lead.get("last_nurture")
                        if last_nurture:
                            try:
                                ln_str = str(last_nurture).replace("Z", "+00:00")
                                ln_dt = datetime.fromisoformat(ln_str)
                                # Garante que ln_dt é tz-aware para comparar com nurture_cooldown
                                if ln_dt.tzinfo is None:
                                    ln_dt = ln_dt.replace(tzinfo=timezone.utc)
                                if ln_dt > nurture_cooldown:
                                    continue  # enviado recente demais
                            except Exception:
                                pass
                        leads_eligible.append(lead)

                    if not leads_eligible:
                        continue

                    logger.info(
                        f"[Follow-up Cold] Stage {stage}: {len(leads_eligible)} leads "
                        f"para tenant {owner_id[:8]}"
                    )

                    for lead in leads_eligible:
                        try:
                            msg = _generate_cold_followup(stage, lead, owner, ai_svc)
                            run_async(wa_svc.send_message(
                                lead["phone"], msg, instance=evolution_instance
                            ))

                            new_stage = stage + 1
                            update_data = {
                                "follow_up_stage": new_stage,
                                "last_nurture": now.isoformat(),
                            }
                            if cfg["final"]:
                                update_data["nurture_paused"] = True
                                logger.info(
                                    f"[Follow-up Cold] {lead['phone']} — ciclo encerrado "
                                    f"(3 estágios completos)"
                                )

                            db.table("customers").update(update_data).eq(
                                "phone", lead["phone"]
                            ).eq("owner_id", owner_id).execute()

                            sent_count += 1
                            logger.info(
                                f"[Follow-up Cold] Stage {stage} enviado para "
                                f"{lead['phone']}: '{msg[:50]}'"
                            )

                        except Exception as e:
                            logger.error(
                                f"[Follow-up Cold] Erro ao enviar para "
                                f"{lead.get('phone')}: {e}"
                            )

                if sent_count:
                    logger.info(
                        f"[Follow-up Cold] Tenant {owner_id[:8]}: "
                        f"{sent_count} mensagem(ns) enviada(s)"
                    )

            except Exception as e:
                logger.error(f"[Follow-up Cold] Erro ao processar tenant {owner_id}: {e}")

            processed_owners.add(owner_id)
            save_progress("follow_up_cold_leads", {"done": list(processed_owners)})

        clear_progress("follow_up_cold_leads")

    except Exception as exc:
        logger.error(f"Erro no follow-up de leads frios: {exc}")
        raise self.retry(exc=exc)


def _generate_cold_followup(stage: int, lead: dict, owner: dict, ai_svc) -> str:
    """Gera mensagem de reengajamento por estágio."""
    summary = lead.get("summary") or ""
    context_summary = owner.get("context_summary") or ""
    bot_tone = owner.get("bot_tone") or "amigável e direto"
    name = lead.get("name") or ""

    stage_instructions = {
        0: (
            "Tom: leve e curioso, sem pressão. "
            "Objetivo: verificar se ficou alguma dúvida da conversa anterior. "
            "Exemplo de saída: 'oi! vi que conversamos há alguns dias — ficou com alguma dúvida?'"
        ),
        1: (
            "Tom: direto e útil, voltado a valor. "
            "Objetivo: reengajar mostrando disponibilidade ou trazendo algum ângulo novo. "
            "Exemplo de saída: 'ainda posso te ajudar! tenho disponibilidade quando quiser conversar.'"
        ),
        2: (
            "Tom: gentil e sem pressão, encerramento natural. "
            "Objetivo: última tentativa, deixar a porta aberta sem cobrar. "
            "Exemplo de saída: 'vou deixar nossa conversa em aberto — é só me chamar quando quiser 😊'"
        ),
    }

    instruction = stage_instructions.get(stage, stage_instructions[2])

    if summary or context_summary:
        try:
            system = (
                f"Você é um atendente de WhatsApp com tom {bot_tone}. "
                "Gere UMA mensagem curta (máximo 2 frases) de reengajamento para um lead silencioso. "
                f"{instruction} "
                "NÃO use o nome do cliente. NÃO invente informações. "
                "NÃO comece com saudação formal. Responda APENAS a mensagem pronta."
            )

            parts = []
            if summary:
                parts.append(f"Histórico do cliente: {summary[:200]}")
            if context_summary:
                parts.append(f"Contexto do negócio: {context_summary[:200]}")
            parts.append("Gere a mensagem de reengajamento:")

            result = run_async(ai_svc.respond(
                system_prompt=system,
                history=[],
                user_message="\n\n".join(parts)
            ))

            if result and len(result.strip()) > 3:
                if name and stage < 2:
                    return f"{name}, {result.strip()}"
                return result.strip()

        except Exception as e:
            logger.warning(f"[Follow-up Cold] IA falhou no stage {stage}: {e}")

    fallback = _COLD_FALLBACKS.get(stage, _COLD_FALLBACKS[2])
    if name and stage < 2:
        return f"{name}, {fallback}"
    return fallback


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5, queue="messages")
@with_ops_alert("nurture_customers")
def nurture_customers(self):
    """Nurture de clientes — itera todos os tenants (beat sem args)."""
    try:
        from app.database import get_db
        from app.services.whatsapp import WhatsAppService
        from app.services.ops import save_progress, get_progress, clear_progress

        db = get_db()
        wa_svc = WhatsAppService()

        progress = get_progress("nurture_customers")
        processed_owners = set(progress.get("done", [])) if progress else set()

        owners_resp = db.table("tenants").select("id, owner_phone, evolution_instance").execute()
        owners = []
        for row in (owners_resp.data or []):
            r = dict(row)
            r.setdefault("phone", r.get("owner_phone", ""))
            owners.append(r)

        if not owners:
            logger.info("[Nurture] Nenhum tenant encontrado")
            return

        for owner in owners:
            owner_id = owner["id"]
            if owner_id in processed_owners:
                continue

            evolution_instance = owner.get("evolution_instance", "")
            if not evolution_instance:
                logger.warning(f"[Nurture] Tenant {owner_id} sem evolution_instance — pulando")
                processed_owners.add(owner_id)
                save_progress("nurture_customers", {"done": list(processed_owners)})
                continue

            try:
                resp = db.table("customers").select("phone, name").eq(
                    "owner_id", owner_id
                ).eq(
                    "lead_status", "cliente"
                ).limit(20).execute()

                customers = resp.data or []
                if not customers:
                    processed_owners.add(owner_id)
                    save_progress("nurture_customers", {"done": list(processed_owners)})
                    continue

                logger.info(f"[Nurture] {len(customers)} clientes para tenant {owner_id}")

                for customer in customers:
                    try:
                        name = customer.get("name") or "você"
                        msg = f"Olá {name}! Obrigado por ser cliente! Quer conhecer nossas novidades? ✨"
                        run_async(wa_svc.send_message(customer["phone"], msg, instance=evolution_instance))
                    except Exception as e:
                        logger.error(f"[Nurture] Erro ao enviar para {customer.get('phone')}: {e}")

                processed_owners.add(owner_id)
                save_progress("nurture_customers", {"done": list(processed_owners)})

            except Exception as e:
                logger.error(f"[Nurture] Erro ao processar tenant {owner_id}: {e}")

        clear_progress("nurture_customers")

    except Exception as exc:
        logger.error(f"Erro no nurture de clientes: {exc}")
        raise self.retry(exc=exc)


# ═══════════════════════════════════════════════════════════════════════════
#  TASKS DE LEARNING
# ═══════════════════════════════════════════════════════════════════════════

@celery_app.task(bind=True, max_retries=3, default_retry_delay=5, queue="learning")
@with_ops_alert("weekly_report")
def weekly_report(self, owner_id: str):
    try:
        from app.services.report import ReportService
        from app.services.alerts import notify_user

        report_svc = ReportService()
        report = report_svc.generate_weekly(owner_id)

        if not report:
            logger.warning(f"[Weekly Report] Nenhum dado para relatório de {owner_id}")
            return

        notify_user(
            owner_id=owner_id,
            title="Relatório Semanal",
            message=report,
            panel_url=_panel_url()
        )
        logger.info(f"[Weekly Report] Enviado para {owner_id}")

    except Exception as exc:
        logger.error(f"Erro ao gerar relatório semanal para {owner_id}: {exc}")
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5, queue="learning")
@with_ops_alert("recalculate_scores")
def recalculate_scores(self, owner_id: str):
    try:
        from app.services.scoring import ScoringService
        scoring_svc = ScoringService()
        updated = scoring_svc.recalculate_all(owner_id)
        logger.info(f"[Recalc Scores] Atualizou {updated} contatos para {owner_id}")

    except Exception as exc:
        logger.error(f"Erro ao recalcular scores para {owner_id}: {exc}")
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5, queue="learning")
@with_ops_alert("nightly_learning")
def nightly_learning(self, owner_id: str):
    try:
        from app.services.learning import LearningService
        learning_svc = LearningService()
        updated = learning_svc.learn_from_conversations(owner_id)
        logger.info(f"[Nightly Learning] Aprendeu de {updated} conversas de {owner_id}")

    except Exception as exc:
        logger.error(f"Erro ao fazer nightly learning para {owner_id}: {exc}")
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5, queue="learning")
@with_ops_alert("nightly_learning_all")
def nightly_learning_all(self):
    """Aprendizado noturno para TODOS os workspaces. Salva progresso."""
    try:
        from app.services.ops import save_progress, get_progress, clear_progress

        progress = get_progress("nightly_learning_all")
        done_ids = set(progress.get("done", [])) if progress else set()

        from app.database import get_db
        db = get_db()
        resp = db.table("tenants").select("id").execute()
        all_owners = [row["id"] for row in (resp.data or [])]

        from app.services.learning import LearningService
        learning_svc = LearningService()

        logger.info(
            f"[Nightly Learning All] Processando {len(all_owners)} tenant(s), "
            f"{len(done_ids)} já feitos"
        )

        for oid in all_owners:
            if oid in done_ids:
                continue
            try:
                updated = learning_svc.learn_from_conversations(oid)
                logger.info(f"[Nightly Learning All] {oid}: aprendeu de {updated} conversas")
            except Exception as e:
                logger.error(f"[Nightly Learning All] Erro em {oid}: {e}")

            done_ids.add(oid)
            save_progress("nightly_learning_all", {"done": list(done_ids)})

        clear_progress("nightly_learning_all")

    except Exception as exc:
        logger.error(f"Erro no nightly learning all: {exc}")
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=60, queue="learning")
@with_ops_alert("daily_web_search")
def daily_web_search(self):
    """
    Busca autônoma diária — cada agente aprende sua especialidade.
    Roda todo dia às 6h BRT, logo após o nightly_learning_all das 3h.
    """
    try:
        from app.database import get_db
        from app.services.web_search import WebSearchService, TOPICS_BY_ROLE

        db = get_db()
        resp = db.table("tenants").select("id").execute()
        all_owners = [row["id"] for row in (resp.data or [])]

        if not all_owners:
            logger.info("[WebSearch] Nenhum tenant encontrado — pulando")
            return

        svc = WebSearchService()
        roles = list(TOPICS_BY_ROLE.keys())
        total_saved = 0

        logger.info(
            "[WebSearch] Iniciando ciclo diário — %d tenant(s) × %d roles",
            len(all_owners), len(roles),
        )

        for owner_id in all_owners:
            tenant_saved = 0
            for role in roles:
                try:
                    saved = svc.search_and_learn(owner_id, role=role)
                    tenant_saved += saved
                    total_saved += saved
                except Exception as e:
                    logger.error(
                        "[WebSearch] Erro no tenant %s role=%s: %s",
                        owner_id[:8], role, e,
                    )
            logger.info(
                "[WebSearch] Tenant %s concluído — %d insights salvos (%d roles)",
                owner_id[:8], tenant_saved, len(roles),
            )

        logger.info(
            "[WebSearch] Ciclo diário concluído — %d insights totais em %d tenant(s)",
            total_saved, len(all_owners),
        )

    except Exception as exc:
        logger.error(f"Erro no daily_web_search: {exc}")
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5, queue="learning")
@with_ops_alert("learn_from_links")
def learn_from_links(self, owner_id: str):
    try:
        from app.services.link_learning import LinkLearningService
        link_svc = LinkLearningService()
        updated = link_svc.process_all_pending_links(owner_id)
        logger.info(f"[Learn from Links] Processou {updated} links para {owner_id}")

    except Exception as exc:
        logger.error(f"Erro ao processar links para {owner_id}: {exc}")
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5, queue="learning")
@with_ops_alert("run_campaign")
def run_campaign(self, campaign_id: str):
    try:
        from app.services.campaign import CampaignService
        campaign_svc = CampaignService()
        campaign_svc.execute(campaign_id)
        logger.info(f"[Campaign] Executada campanha {campaign_id}")

    except Exception as exc:
        logger.error(f"Erro ao executar campanha {campaign_id}: {exc}")
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5, queue="learning")
@with_ops_alert("daily_backup")
def daily_backup(self):
    """Backup diário via Supabase Storage."""
    try:
        from app.services.backup import run_backup
        result = run_backup()
        logger.info(f"[Daily Backup] OK — {result.get('total_rows', 0)} registros")

    except Exception as exc:
        logger.error(f"Erro ao fazer backup: {exc}")
        raise self.retry(exc=exc)


# ═══════════════════════════════════════════════════════════════════════════
#  TASKS DE OPS
# ═══════════════════════════════════════════════════════════════════════════

@celery_app.task(bind=True, max_retries=1, default_retry_delay=30, queue="learning")
def health_check(self):
    """Roda a cada 30 min. Verifica componentes e alerta se algo está degradado."""
    try:
        from app.services.ops import run_health_check
        from app.services.alerts import notify_warn

        report = run_health_check()

        if report["overall"] != "healthy":
            problems = []
            for comp, info in report.get("components", {}).items():
                if info["status"] != "ok":
                    problems.append(f"`{comp}`: {info['status']}")
            for task, info in report.get("circuits", {}).items():
                ttl = info.get("ttl_seconds", 0)
                problems.append(f"Circuit `{task}` aberto ({ttl // 60}min restantes)")
            if problems:
                notify_warn(f"Health Check — DEGRADADO\n\n" + "\n".join(problems))

        logger.info(f"[Health Check] Status: {report['overall']}")

    except Exception as exc:
        logger.error(f"Erro no health check: {exc}")
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=1, default_retry_delay=30, queue="learning")
def daily_ops_report(self):
    """Relatório de ops a cada 6h no Telegram."""
    try:
        from app.services.ops import generate_ops_report
        from app.services.alerts import notify_owner

        report = generate_ops_report()
        notify_owner(report, level="info")
        logger.info("[Ops Report] Enviado")

    except Exception as exc:
        logger.error(f"Erro ao gerar ops report: {exc}")
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=1, default_retry_delay=60, queue="learning")
def sentinel_monitor(self):
    """
    Ciclo de monitoramento autônomo do Sentinel.
    Roda a cada 5 minutos via Celery Beat.
    """
    try:
        from app.agents.registry import load_all_agents, get_agent
        from app.agents.base import AgentContext

        load_all_agents()
        sentinel = get_agent("sentinel")
        if not sentinel:
            logger.warning("[sentinel_monitor] Sentinel não encontrado no registry")
            return

        context = AgentContext(
            tenant_id="system",
            triggered_by="celery_beat",
            payload={"source": "scheduled"},
        )

        findings = run_async(sentinel.act(context))
        status = findings.get("status", "unknown")
        anomaly_count = len(findings.get("anomalies", []))

        logger.info("[sentinel_monitor] Status=%s, anomalias=%d", status, anomaly_count)

        if anomaly_count > 0:
            doctor = get_agent("doctor")
            if doctor:
                import uuid
                incident_id = str(uuid.uuid4())[:8]
                doctor_context = AgentContext(
                    tenant_id="system",
                    triggered_by="sentinel",
                    incident_id=incident_id,
                    payload={
                        "anomaly": findings,
                        "anomalies": findings.get("anomalies", []),
                        "triggered_by_sentinel": True,
                    },
                )
                diagnosis = run_async(doctor.act(doctor_context))

                if diagnosis.get("ready_for_surgeon"):
                    surgeon = get_agent("surgeon")
                    if surgeon:
                        surgeon_context = AgentContext(
                            tenant_id="system",
                            triggered_by="doctor",
                            incident_id=incident_id,
                            payload={"diagnosis": diagnosis},
                        )
                        run_async(surgeon.act(surgeon_context))

    except Exception as exc:
        logger.error(f"[sentinel_monitor] Erro: {exc}")
        raise self.retry(exc=exc)


# ═══════════════════════════════════════════════════════════════════════════
#  FUNCS AUXILIARES
# ═══════════════════════════════════════════════════════════════════════════

async def _dispatch_to_agent(phone: str, owner_id: str, message: str, agent_mode: str, **kwargs):
    """Despacha a mensagem para o agente."""
    from app.services.agent import AgentService

    agent = AgentService(owner_id)
    response = await agent.respond(
        phone=phone,
        message=message,
        agent_mode=agent_mode,
        **kwargs
    )
    return response
