"""
EcoZap — Doctor Agent
======================
Papel: Diagnóstico de erros e incidentes.
Hierarquia: Especialista → OPS → CTO → CEO

Responsabilidades:
- Recebe anomalia do Sentinel
- Acessa logs detalhados, stack traces, contexto
- Identifica arquivo + linha + causa raiz
- Cria relatório de diagnóstico estruturado
- Passa diagnóstico para o Surgeon

Opinion bias: "Científico e metódico. Não passa para o Surgeon sem causa raiz confirmada."
"""
import logging
from datetime import datetime, timezone
from app.agents.base import Agent, AgentContext, AgentOpinion, AuthorityLevel
from app.agents.registry import register

logger = logging.getLogger(__name__)


@register
class Doctor(Agent):
    role = "doctor"
    display_name = "Doctor"
    authority_level = AuthorityLevel.SPECIALIST
    department = "ops"
    opinion_bias = "científico e metódico — não passa adiante sem causa raiz confirmada"

    autonomous_actions = [
        "read_logs",
        "access_supabase_logs",
        "access_railway_logs",
        "create_incident",
        "generate_diagnosis",
    ]
    requires_ceo_override = [
        "deploy_to_production",
        "merge_to_main",
        "alter_database_schema",
    ]

    async def act(self, context: AgentContext) -> dict:
        """
        Diagnostica um incidente recebido do Sentinel.
        Retorna diagnóstico estruturado para o Surgeon.
        """
        anomaly = context.payload.get("anomaly", {})
        logger.info(f"[Doctor] Diagnosticando: {anomaly}")

        diagnosis = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "incident_id": context.incident_id,
            "anomaly": anomaly,
            "root_cause": None,
            "affected_file": None,
            "affected_lines": [],
            "confidence": 0.0,
            "recommended_fix": None,
            "needs_ceo_override": False,
        }

        # TODO Sprint 1: implementar diagnóstico real
        # 1. Acessar logs Railway via API
        # 2. Extrair stack trace completo
        # 3. Identificar padrão de erro (banco, rede, código, config)
        # 4. Mapear para arquivo + linha via traceback
        # 5. Classificar severidade
        # 6. Gerar recomendação de fix

        logger.info(f"[Doctor] Diagnóstico concluído. Causa raiz: {diagnosis['root_cause']}")
        return diagnosis

    async def report_status(self) -> dict:
        return {
            "role": self.role,
            "status": "operational",
            "last_diagnosis": None,
            "summary": "Diagnóstico sob demanda. Aguardando incidentes.",
        }

    def opine(self, question: str, context: AgentContext) -> AgentOpinion:
        return AgentOpinion(
            agent_role=self.role,
            agrees=True,
            reasoning=(
                f"[{self.display_name}] Qualquer mudança crítica deve ter "
                f"diagnóstico de estado atual antes e validação depois."
            ),
        )
