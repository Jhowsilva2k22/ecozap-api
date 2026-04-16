"""
EcoZap — Attendant Agent
=========================
Papel: Atendimento 24/7 — o agente que já existe, refatorado pro padrão base.
Hierarquia: Especialista → COMMERCIAL → CTO → CEO

Responsabilidades:
- Responde mensagens WhatsApp e Instagram
- Qualifica leads (lead_score automático)
- Conduz conversa até conversão ou agendamento
- Aprende com cada interação (nightly_learning)

Opinion bias: "Orientado ao cliente — prefere pecar pelo excesso de atenção."
"""
import logging
from datetime import datetime, timezone
from app.agents.base import Agent, AgentContext, AgentOpinion, AuthorityLevel
from app.agents.registry import register

logger = logging.getLogger(__name__)


@register
class Attendant(Agent):
    role = "attendant"
    display_name = "Attendant"
    authority_level = AuthorityLevel.SPECIALIST
    department = "commercial"
    opinion_bias = "orientado ao cliente — prefere pecar pelo excesso de atenção"

    autonomous_actions = [
        "send_message",
        "update_lead_score",
        "schedule_followup",
        "read_knowledge_base",
        "create_customer",
    ]
    requires_ceo_override = [
        "broadcast_to_all_customers",
        "delete_customer",
        "change_pricing",
    ]

    async def act(self, context: AgentContext) -> dict:
        """
        Processa mensagem de cliente e gera resposta.
        Delega ao app/agents/attendant.py legado por enquanto.
        TODO: migrar lógica completa aqui na Sprint 2.
        """
        logger.info(f"[Attendant] Processando mensagem. Context: {context.tenant_id}")
        # Delegação ao módulo legado via import dinâmico
        # Será migrado integralmente na Sprint 2
        return {
            "status": "delegated_to_legacy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def report_status(self) -> dict:
        return {
            "role": self.role,
            "status": "operational",
            "summary": "Atendimento 24/7 ativo. Processando mensagens via webhook.",
        }

    def opine(self, question: str, context: AgentContext) -> AgentOpinion:
        customer_keywords = ["cliente", "lead", "mensagem", "atendimento", "resposta"]
        if any(kw in question.lower() for kw in customer_keywords):
            return AgentOpinion(
                agent_role=self.role,
                agrees=True,
                reasoning=(
                    f"[{self.display_name}] Qualquer mudança no fluxo de atendimento "
                    f"deve ser testada primeiro em conversa de teste, não com leads reais."
                ),
            )
        return AgentOpinion(
            agent_role=self.role,
            agrees=True,
            reasoning=f"[{self.display_name}] Sem impacto no atendimento identificado.",
        )


# ─── Placeholders de negócio (futuro) ────────────────────────────────────────
# Cada um será um arquivo próprio quando implementado.
# Registrados aqui como placeholder para o organograma ficar completo.

class _SDRPlaceholder:
    """SDR — Qualifica leads. Sprint 2."""
    role = "sdr"
    display_name = "SDR"
    department = "commercial"
    authority_level = AuthorityLevel.SPECIALIST

class _CloserPlaceholder:
    """Closer — Fecha vendas. Sprint 2."""
    role = "closer"
    display_name = "Closer"
    department = "commercial"
    authority_level = AuthorityLevel.SPECIALIST

class _ConsultantPlaceholder:
    """Consultant — Educa e retém. Sprint 2."""
    role = "consultant"
    display_name = "Consultant"
    department = "commercial"
    authority_level = AuthorityLevel.SPECIALIST

class _TrafficPlaceholder:
    """Traffic — Tráfego pago. Sprint 3."""
    role = "traffic"
    display_name = "Traffic"
    department = "growth"
    authority_level = AuthorityLevel.SPECIALIST

class _AnalystPlaceholder:
    """Analyst — KPIs e relatórios. Sprint 3."""
    role = "analyst"
    display_name = "Analyst"
    department = "intelligence"
    authority_level = AuthorityLevel.SPECIALIST
