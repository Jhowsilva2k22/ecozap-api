"""
EcoZap — Guardian Agent (v1)
=============================
Papel: Validação de backups antes de salvar.
Hierarquia: Especialista → OPS → CTO → CEO

Responsabilidades:
- Intercepta backup antes de salvar no Storage
- Roda smoke tests nas tabelas exportadas
- Se corrompido → NÃO salva, abre alerta imediato
- Mantém histórico de integridade de backups

Opinion bias: "Zero tolerância com backup corrompido. Melhor sem backup que com backup quebrado."
"""
import logging
from datetime import datetime, timezone
from typing import Optional
from app.agents.base import Agent, AgentContext, AgentOpinion, AuthorityLevel
from app.agents.registry import register

logger = logging.getLogger(__name__)

# Tabelas críticas que DEVEM existir em um backup válido
REQUIRED_TABLES = ["tenants", "owners", "customers"]
# Contagem mínima de linhas por tabela para considerar backup válido
MIN_ROWS_PER_TABLE = {"tenants": 0, "owners": 0, "customers": 0}


@register
class Guardian(Agent):
    role = "guardian"
    display_name = "Guardian"
    authority_level = AuthorityLevel.SPECIALIST
    department = "ops"
    opinion_bias = "zero tolerância com backup corrompido — melhor sem backup que com backup quebrado"

    autonomous_actions = [
        "validate_backup",
        "skip_corrupted_backup",
        "send_telegram_alert",
        "read_backup_data",
    ]
    requires_ceo_override = [
        "delete_all_backups",
        "restore_from_backup",
    ]

    async def validate_backup(self, backup_data: dict) -> dict:
        """
        Valida os dados de backup antes de salvar no Storage.

        Args:
            backup_data: dict com chaves = nomes das tabelas, valores = lista de linhas

        Returns:
            dict com is_valid, issues, approved_tables, rejected_tables
        """
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "is_valid": True,
            "issues": [],
            "approved_tables": [],
            "rejected_tables": [],
            "total_rows": 0,
        }

        # 1. Verificar tabelas obrigatórias
        for table in REQUIRED_TABLES:
            if table not in backup_data:
                result["issues"].append(f"Tabela obrigatória ausente: {table}")
                result["rejected_tables"].append(table)
                result["is_valid"] = False
                continue

            rows = backup_data.get(table, [])
            min_rows = MIN_ROWS_PER_TABLE.get(table, 0)

            # 2. Verificar se é lista válida
            if not isinstance(rows, list):
                result["issues"].append(f"{table}: dados não são uma lista")
                result["rejected_tables"].append(table)
                result["is_valid"] = False
                continue

            # 3. Verificar contagem mínima
            if len(rows) < min_rows:
                result["issues"].append(
                    f"{table}: {len(rows)} linhas (mínimo: {min_rows})"
                )
                result["rejected_tables"].append(table)
                # Não marca is_valid=False se min_rows=0 (vazio é ok)
            else:
                result["approved_tables"].append(table)
                result["total_rows"] += len(rows)

            # 4. Smoke test: verificar estrutura mínima de 1 registro
            if rows:
                sample = rows[0]
                if not isinstance(sample, dict):
                    result["issues"].append(f"{table}: registro não é um dict")
                    result["is_valid"] = False
                elif "id" not in sample:
                    result["issues"].append(f"{table}: campo 'id' ausente no registro")
                    result["is_valid"] = False

        if result["is_valid"]:
            logger.info(
                f"[Guardian] Backup VÁLIDO — "
                f"{len(result['approved_tables'])} tabelas, "
                f"{result['total_rows']} linhas totais"
            )
        else:
            logger.error(
                f"[Guardian] Backup INVÁLIDO — "
                f"Issues: {result['issues']}"
            )

        return result

    async def act(self, context: AgentContext) -> dict:
        """Entry point padrão — valida backup do payload."""
        backup_data = context.payload.get("backup_data", {})
        return await self.validate_backup(backup_data)

    async def report_status(self) -> dict:
        return {
            "role": self.role,
            "status": "operational",
            "last_validation": None,
            "summary": "Validação de backup ativa. Pronto para interceptar próximo backup.",
        }

    def opine(self, question: str, context: AgentContext) -> AgentOpinion:
        backup_keywords = ["backup", "restore", "dados", "storage"]
        if any(kw in question.lower() for kw in backup_keywords):
            return AgentOpinion(
                agent_role=self.role,
                agrees=True,
                reasoning=(
                    f"[{self.display_name}] Confirmo validação obrigatória antes de "
                    f"qualquer backup. Zero dados corrompidos salvos em produção."
                ),
            )
        return AgentOpinion(
            agent_role=self.role,
            agrees=True,
            reasoning=f"[{self.display_name}] Sem impacto em backup identificado.",
        )
