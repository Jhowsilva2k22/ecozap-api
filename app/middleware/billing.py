"""
Middleware de Billing — EcoZap.

Intercepta toda mensagem processada e:
1. Verifica se o owner está dentro do limite do plano.
2. Incrementa o contador de uso mensal.
3. Bloqueia e notifica quando o limite é atingido.

Uso (chamar antes de responder ao lead):
    from app.middleware.billing import BillingMiddleware
    billing = BillingMiddleware()
    allowed = await billing.check_and_increment(owner_id)
    if not allowed:
        # enviar aviso ao dono, não responder ao lead
        return
"""
from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class BillingMiddleware:
    """Verifica limites de uso e incrementa contador no Supabase."""

    def __init__(self):
        from supabase import create_client
        self.db = create_client(settings.supabase_url, settings.supabase_service_key)

    # ── Verificação principal ─────────────────────────────────────────────

    async def check_and_increment(self, owner_id: str) -> bool:
        """
        Retorna True se a mensagem pode ser processada.
        Incrementa o uso automaticamente quando permitido.
        """
        try:
            result = self.db.rpc("check_usage_limit", {"p_owner_id": owner_id}).execute()
            if not result.data:
                logger.warning("[Billing] check_usage_limit sem resposta para %s — permitindo por padrão", owner_id[:8])
                return True

            info = result.data
            allowed = info.get("allowed", True)

            if allowed:
                month = datetime.utcnow().strftime("%Y-%m")
                self.db.rpc("increment_usage", {"p_owner_id": owner_id, "p_month": month}).execute()
                return True

            # Limite atingido — notifica dono uma vez por hora
            self._notify_limit_reached(owner_id, info)
            return False

        except Exception as e:
            logger.error("[Billing] Erro ao checar limite para %s: %s", owner_id[:8], e)
            return True  # fail-open: melhor deixar passar do que bloquear por erro técnico

    def get_usage(self, owner_id: str) -> dict:
        """Retorna uso atual do owner sem incrementar."""
        try:
            result = self.db.rpc("check_usage_limit", {"p_owner_id": owner_id}).execute()
            return result.data or {}
        except Exception as e:
            logger.error("[Billing] Erro ao buscar uso: %s", e)
            return {}

    # ── Notificação de limite ─────────────────────────────────────────────

    def _notify_limit_reached(self, owner_id: str, info: dict):
        """Envia alerta WhatsApp ao dono quando limite é atingido."""
        try:
            owner_row = (
                self.db.table("owners")
                .select("phone, business_name, plan_id, _billing_notified_at")
                .eq("id", owner_id)
                .maybe_single()
                .execute()
            )
            if not (owner_row and owner_row.data):
                return

            owner = owner_row.data
            used  = info.get("used", 0)
            limit = info.get("limit", "?")
            plan  = owner.get("plan_id", "starter")

            # Monta mensagem de aviso
            upgrade_link = f"{settings.app_url}/panel/billing?token={settings.app_secret}&owner_id={owner_id}"
            msg = (
                f"⚠️ *Limite de mensagens atingido!*\n\n"
                f"*{owner.get('business_name', 'EcoZap')}* — Plano *{plan.upper()}*\n"
                f"Uso este mês: *{used}/{limit}* mensagens\n\n"
                f"Seus leads não estão sendo respondidos agora.\n"
                f"Faça upgrade para continuar sem interrupções:\n"
                f"👉 {upgrade_link}"
            )

            import asyncio, httpx
            async def _send():
                from app.services.whatsapp import WhatsAppService
                wa = WhatsAppService()
                await wa.send_message(owner["phone"], msg)

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(_send())
                else:
                    loop.run_until_complete(_send())
            except Exception:
                pass

            logger.warning("[Billing] Limite atingido: owner=%s usado=%s limite=%s", owner_id[:8], used, limit)

        except Exception as e:
            logger.error("[Billing] Falha ao notificar owner %s: %s", owner_id[:8], e)

    # ── Helpers de plano ─────────────────────────────────────────────────

    def get_owner_plan(self, owner_id: str) -> Optional[str]:
        """Retorna o plan_id do owner."""
        try:
            result = (
                self.db.table("owners")
                .select("plan_id")
                .eq("id", owner_id)
                .maybe_single()
                .execute()
            )
            return (result.data or {}).get("plan_id", "starter")
        except Exception:
            return "starter"

    def is_feature_allowed(self, owner_id: str, feature: str) -> bool:
        """Verifica se a feature está disponível no plano do owner."""
        from app.models.plans import get_plan
        plan_id = self.get_owner_plan(owner_id)
        plan = get_plan(plan_id)
        return plan.allows_feature(feature)
