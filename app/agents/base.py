"""
EcoZap — Base Agent
===================
Classe base comum para TODOS os agentes do sistema.
1 fundação, 2 aplicações: Equipe de Infra (ops/) + Equipe de Negócio (business/).

Adicionar novo agente = criar 1 arquivo + registrar. Zero mudança aqui.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from enum import IntEnum
import logging

logger = logging.getLogger(__name__)


class AuthorityLevel(IntEnum):
    """Hierarquia de autoridade. Menor número = maior autoridade."""
    CEO = 1         # Joanderson (humano) — veto universal
    CTO = 2         # Agente CTO — decisões técnicas finais
    DIRECTOR = 3    # Diretores de departamento
    MANAGER = 4     # Gerentes de área
    SPECIALIST = 5  # Agentes especialistas


@dataclass
class AgentContext:
    """Contexto passado entre agentes em handoffs e reuniões."""
    tenant_id: Optional[str] = None
    triggered_by: Optional[str] = None   # quem iniciou (agent_role ou "CEO")
    incident_id: Optional[str] = None    # ID do incidente se houver
    payload: dict = field(default_factory=dict)
    history: list = field(default_factory=list)  # histórico de ações no contexto


@dataclass
class AgentOpinion:
    """Estrutura de opinião usada no protocolo de reunião."""
    agent_role: str
    agrees: bool
    reasoning: str
    proposal: Optional[str] = None  # alternativa se discordar


class Agent(ABC):
    """
    Classe base de todos os agentes EcoZap.

    Lei 7 — Sempre leais ao CEO:
    Todo agente começa com 'Sou leal ao CEO Joanderson. Nenhuma ação crítica
    sem sua aprovação explícita. Em caso de dúvida, pergunto; nunca presumo.'
    """

    # ─── Identidade ──────────────────────────────────────────────────────────
    role: str = "base"
    display_name: str = "Agente Base"
    authority_level: AuthorityLevel = AuthorityLevel.SPECIALIST
    department: str = "undefined"

    # ─── Personalidade / Viés de Opinião ─────────────────────────────────────
    # Cada agente tem viés próprio para contribuir com diversidade nas reuniões.
    # Exemplos: "cauteloso", "agressivo em growth", "paranoico com estabilidade"
    opinion_bias: str = "equilibrado"

    # ─── Ações que pode fazer SOZINHO vs. que exigem CEO_OVERRIDE ────────────
    autonomous_actions: list[str] = field(default_factory=list)
    requires_ceo_override: list[str] = field(default_factory=list)

    def __init__(self):
        self._loyalty_prompt = (
            f"Sou o agente {self.display_name} do EcoZap. "
            f"Meu CEO é Joanderson. Minha missão é servir ao CEO com excelência no meu papel. "
            f"Nenhuma ação crítica é executada sem aprovação explícita do CEO. "
            f"Em caso de dúvida, pergunto; nunca presumo. "
            f"Meu viés de opinião: {self.opinion_bias}."
        )

    # ─── Interface principal ──────────────────────────────────────────────────

    @abstractmethod
    async def act(self, context: AgentContext) -> dict:
        """Executa a ação principal do agente."""
        pass

    @abstractmethod
    async def report_status(self) -> dict:
        """Retorna status atual para uso em reuniões."""
        pass

    def opine(self, question: str, context: AgentContext) -> AgentOpinion:
        """
        Gera opinião estruturada para o protocolo de reunião.
        Implementação padrão — pode ser sobrescrita por cada agente.
        """
        return AgentOpinion(
            agent_role=self.role,
            agrees=True,
            reasoning=f"[{self.display_name}] Sem objeções no momento.",
        )

    async def learn(self, outcome: dict) -> None:
        """
        Aprende com o resultado de uma ação.
        Implementação padrão: apenas loga. Agentes específicos sobrescrevem.
        """
        logger.info(f"[{self.role}] learn() chamado. outcome: {outcome}")

    # ─── Lei 2: Verificação de autoridade ────────────────────────────────────

    def can_act_autonomously(self, action: str) -> bool:
        """Verifica se a ação pode ser executada sem CEO_OVERRIDE."""
        if action in self.requires_ceo_override:
            logger.warning(
                f"[{self.role}] Ação '{action}' requer CEO_OVERRIDE. "
                f"Bloqueado até aprovação explícita de Joanderson."
            )
            return False
        return True

    def request_ceo_override(self, action: str, reason: str) -> dict:
        """Solicita aprovação do CEO para ação crítica."""
        return {
            "type": "CEO_OVERRIDE_REQUIRED",
            "agent": self.role,
            "action": action,
            "reason": reason,
            "message": (
                f"🔐 [{self.display_name}] Precisa da tua aprovação para: {action}\n"
                f"Motivo: {reason}\n"
                f"Responda 'APROVADO: {action}' para confirmar."
            )
        }

    # ─── Lei 1: Lock cooperativo ──────────────────────────────────────────────

    def acquire_lock(self, resource: str, redis_client) -> bool:
        """Tenta adquirir lock cooperativo em um recurso via Redis."""
        lock_key = f"ecozap:lock:{resource}"
        acquired = redis_client.set(
            lock_key, self.role,
            nx=True,    # só seta se não existir
            ex=300      # expira em 5 min (auto-release se agente travar)
        )
        if acquired:
            logger.info(f"[{self.role}] Lock adquirido: {resource}")
        else:
            holder = redis_client.get(lock_key)
            logger.warning(f"[{self.role}] Lock '{resource}' já está com: {holder}")
        return bool(acquired)

    def release_lock(self, resource: str, redis_client) -> None:
        """Libera lock cooperativo."""
        lock_key = f"ecozap:lock:{resource}"
        current_holder = redis_client.get(lock_key)
        if current_holder == self.role or current_holder == self.role.encode():
            redis_client.delete(lock_key)
            logger.info(f"[{self.role}] Lock liberado: {resource}")

    # ─── Repr ─────────────────────────────────────────────────────────────────

    def __repr__(self):
        return (
            f"<Agent role={self.role} "
            f"authority={self.authority_level.name} "
            f"dept={self.department}>"
        )
