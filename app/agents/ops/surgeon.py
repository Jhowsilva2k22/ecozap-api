"""
EcoZap — Surgeon Agent
=======================
Papel: Geração e aplicação de patches.
Hierarquia: Especialista → OPS → CTO → CEO

Responsabilidades:
- Recebe diagnóstico do Doctor
- Gera patch mínimo e cirúrgico
- Cria Pull Request no GitHub
- Se testes passam + CEO aprova → merge automático
- Dispara redeploy no Railway

CEO_OVERRIDE OBRIGATÓRIO para:
- merge_to_main
- deploy_to_production

Opinion bias: "Cirúrgico. Fix mínimo, sem side effects. Prefere esperar aprovação a errar."
"""
import logging
from datetime import datetime, timezone
from app.agents.base import Agent, AgentContext, AgentOpinion, AuthorityLevel
from app.agents.registry import register

logger = logging.getLogger(__name__)


@register
class Surgeon(Agent):
    role = "surgeon"
    display_name = "Surgeon"
    authority_level = AuthorityLevel.SPECIALIST
    department = "ops"
    opinion_bias = "cirúrgico — fix mínimo, zero side effects, prefere aguardar aprovação"

    autonomous_actions = [
        "read_codebase",
        "generate_patch",
        "run_local_tests",
        "create_pull_request",
    ]
    requires_ceo_override = [
        "merge_to_main",
        "deploy_to_production",
        "alter_database_schema",
        "delete_data",
    ]

    async def act(self, context: AgentContext) -> dict:
        """
        Gera patch com base no diagnóstico do Doctor.
        NÃO aplica sem CEO_OVERRIDE para merge/deploy.
        """
        diagnosis = context.payload.get("diagnosis", {})
        logger.info(f"[Surgeon] Gerando patch para: {diagnosis.get('root_cause')}")

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "diagnosis": diagnosis,
            "patch_generated": False,
            "pr_url": None,
            "awaiting_ceo_approval": False,
            "deployed": False,
        }

        # TODO Sprint 1: implementar geração real de patch
        # 1. Ler arquivo afetado (diagnosis.affected_file)
        # 2. Gerar patch via Claude API com contexto do diagnóstico
        # 3. Validar patch (sintaxe, imports, lógica básica)
        # 4. Criar branch fix/surgeon-{incident_id}
        # 5. Commit patch na branch
        # 6. Criar PR com descrição detalhada
        # 7. Solicitar CEO_OVERRIDE para merge
        # → resultado: PR URL + mensagem Telegram pro CEO aprovar

        if result["pr_url"]:
            result["awaiting_ceo_approval"] = True
            approval_request = self.request_ceo_override(
                action="merge_to_main",
                reason=f"Patch gerado para: {diagnosis.get('root_cause')}. PR: {result['pr_url']}"
            )
            logger.info(f"[Surgeon] Aguardando CEO_OVERRIDE: {approval_request['message']}")

        return result

    async def report_status(self) -> dict:
        return {
            "role": self.role,
            "status": "operational",
            "pending_prs": 0,
            "summary": "Pronto para gerar patches. Sem PRs pendentes.",
        }

    def opine(self, question: str, context: AgentContext) -> AgentOpinion:
        deploy_keywords = ["deploy", "merge", "production", "release"]
        if any(kw in question.lower() for kw in deploy_keywords):
            return AgentOpinion(
                agent_role=self.role,
                agrees=True,
                reasoning=(
                    f"[{self.display_name}] Confirmo que qualquer merge/deploy "
                    f"deve ter CEO_OVERRIDE explícito. Já está na minha whitelist."
                ),
            )
        return AgentOpinion(
            agent_role=self.role,
            agrees=True,
            reasoning=f"[{self.display_name}] Sem impacto de patch identificado.",
        )
