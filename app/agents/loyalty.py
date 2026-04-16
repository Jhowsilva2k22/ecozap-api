"""
EcoZap — Loyalty & Governance
==============================
Implementa a Lei 7: "Sempre leais ao CEO Joanderson."

Componentes:
- CEO_OVERRIDE: invariant de aprovação para ações críticas
- AuditLog: registro imutável de todas as decisões dos agentes
- Whitelist: o que cada agente pode fazer sozinho vs. precisa de aprovação
"""
from datetime import datetime, timezone
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# ─── Whitelist global de ações ────────────────────────────────────────────────

AUTONOMOUS_ACTIONS = {
    # Qualquer agente pode fazer sozinho
    "*": [
        "read_logs",
        "read_metrics",
        "generate_report",
        "send_telegram_alert",
        "health_check",
    ],
    # Por agente específico
    "sentinel": ["restart_service", "trigger_health_check"],
    "doctor":   ["generate_diagnosis", "create_incident"],
    "surgeon":  ["generate_patch", "create_pull_request"],
    "guardian": ["validate_backup", "skip_corrupted_backup"],
    "attendant":["send_message", "update_lead_score", "schedule_followup"],
}

CEO_OVERRIDE_REQUIRED = [
    "merge_to_main",
    "deploy_to_production",
    "alter_database_schema",
    "broadcast_to_all_customers",
    "delete_data",
    "change_pricing",
    "create_new_service",
    "modify_auth_config",
    "spend_above_quota",
    "execute_financial_transaction",
]


def can_act_autonomously(agent_role: str, action: str) -> bool:
    """Verifica se uma ação pode ser executada sem aprovação do CEO."""
    if action in CEO_OVERRIDE_REQUIRED:
        return False
    allowed = AUTONOMOUS_ACTIONS.get("*", []) + AUTONOMOUS_ACTIONS.get(agent_role, [])
    return action in allowed


def requires_override(action: str) -> bool:
    """Verifica se uma ação está na lista de CEO_OVERRIDE."""
    return action in CEO_OVERRIDE_REQUIRED


# ─── Audit Log ────────────────────────────────────────────────────────────────

class AuditLog:
    """
    Registro imutável de todas as decisões dos agentes.
    Salva em Supabase tabela agent_audit_log.
    """

    def __init__(self, db_client=None):
        self.db = db_client

    async def record(
        self,
        agent_role: str,
        action: str,
        context: dict,
        outcome: str,
        approved_by: Optional[str] = None,
        ceo_override: bool = False,
    ) -> dict:
        """Registra uma decisão no audit log."""
        entry = {
            "agent_role": agent_role,
            "action": action,
            "context": context,
            "outcome": outcome,
            "approved_by": approved_by or ("CEO_AUTO" if not ceo_override else "CEO_EXPLICIT"),
            "ceo_override_required": ceo_override,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if self.db:
            try:
                self.db.table("agent_audit_log").insert(entry).execute()
            except Exception as e:
                logger.error(f"[AuditLog] Falha ao salvar entrada: {e}")
                # Log local como fallback
                logger.info(f"[AuditLog] FALLBACK — {entry}")
        else:
            logger.info(f"[AuditLog] {agent_role} → {action} → {outcome}")

        return entry

    async def get_recent(self, limit: int = 20) -> list[dict]:
        """Retorna as entradas mais recentes do audit log."""
        if not self.db:
            return []
        try:
            resp = self.db.table("agent_audit_log")\
                .select("*")\
                .order("timestamp", desc=True)\
                .limit(limit)\
                .execute()
            return resp.data or []
        except Exception as e:
            logger.error(f"[AuditLog] Erro ao buscar entradas: {e}")
            return []


# ─── CEO Override Request ─────────────────────────────────────────────────────

def format_override_request(agent_role: str, action: str, reason: str, context: dict = None) -> str:
    """
    Formata mensagem de solicitação de CEO_OVERRIDE para envio via Telegram.
    O CEO responde 'APROVADO: {action}' para confirmar.
    """
    ctx_summary = ""
    if context:
        ctx_items = [f"  • {k}: {v}" for k, v in list(context.items())[:5]]
        ctx_summary = "\n" + "\n".join(ctx_items)

    return (
        f"🔐 *CEO OVERRIDE SOLICITADO*\n\n"
        f"*Agente:* {agent_role}\n"
        f"*Ação:* `{action}`\n"
        f"*Motivo:* {reason}"
        f"{ctx_summary}\n\n"
        f"Para aprovar, responda:\n"
        f"`APROVADO: {action}`\n\n"
        f"Para rejeitar:\n"
        f"`REJEITADO: {action}`"
    )
