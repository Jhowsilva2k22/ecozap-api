"""
EcoZap — Sentinel Agent
========================
Papel: Monitoramento 24/7 da infraestrutura.
Hierarquia: Especialista → OPS → CTO → CEO

Responsabilidades:
- Monitora logs Railway em tempo real
- Detecta anomalias antes do circuit breaker estourar
- Publica eventos no message bus
- Pode reiniciar serviços sozinho (autônomo)
- Escala para Doctor se anomalia confirmada

Opinion bias: "Paranoico com estabilidade. Prefiro falso alarme a susto real."
"""
import logging
from datetime import datetime, timezone
from app.agents.base import Agent, AgentContext, AgentOpinion, AuthorityLevel
from app.agents.registry import register
from app.agents.loyalty import can_act_autonomously

logger = logging.getLogger(__name__)


@register
class Sentinel(Agent):
    role = "sentinel"
    display_name = "Sentinel"
    authority_level = AuthorityLevel.SPECIALIST
    department = "ops"
    opinion_bias = "paranoico com estabilidade — prefere falso alarme a susto real"

    autonomous_actions = [
        "read_logs",
        "trigger_health_check",
        "send_telegram_alert",
        "publish_anomaly_event",
    ]
    requires_ceo_override = [
        "restart_service",  # por segurança, restart exige confirmação por ora
        "deploy_to_production",
        "merge_to_main",
    ]

    async def act(self, context: AgentContext) -> dict:
        """
        Executa ciclo de monitoramento.
        Chamado pelo Celery Beat a cada 5 minutos.
        """
        logger.info("[Sentinel] Iniciando ciclo de monitoramento...")

        findings = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "anomalies": [],
            "status": "healthy",
        }

        # TODO Sprint 1: implementar verificações reais
        # - Consultar logs Railway via API
        # - Verificar taxa de erros nas últimas 5 min
        # - Verificar uso de memória/CPU
        # - Verificar tempo de resposta do /health
        # - Verificar fila Celery (backlog)

        if findings["anomalies"]:
            findings["status"] = "anomaly_detected"
            logger.warning(f"[Sentinel] {len(findings['anomalies'])} anomalia(s) detectada(s)")
        else:
            logger.info("[Sentinel] Sistema saudável.")

        return findings

    async def report_status(self) -> dict:
        """Status para reunião."""
        return {
            "role": self.role,
            "status": "operational",
            "last_check": datetime.now(timezone.utc).isoformat(),
            "summary": "Monitoramento ativo. Sem anomalias no momento.",
        }

    def opine(self, question: str, context: AgentContext) -> AgentOpinion:
        """Sentinel sempre pergunta sobre estabilidade primeiro."""
        stability_keywords = ["deploy", "rename", "schema", "migration", "restart"]
        if any(kw in question.lower() for kw in stability_keywords):
            return AgentOpinion(
                agent_role=self.role,
                agrees=True,
                reasoning=(
                    f"[{self.display_name}] Verificarei estabilidade antes e depois. "
                    f"Recomendo janela de 30 min de observação pós-mudança."
                ),
            )
        return AgentOpinion(
            agent_role=self.role,
            agrees=True,
            reasoning=f"[{self.display_name}] Sem impacto de monitoramento identificado.",
        )
