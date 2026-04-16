"""
EcoZap — Council (Protocolo de Reunião)
========================================
Lei 4: Reunião sob demanda.
Lei 5: Todos têm espaço de fala.
Lei 6: Opinião com alinhamento.

Ativado por:
- Comando WhatsApp: /reuniao [assunto]
- Endpoint: POST /api/council/meeting
- Telegram: resposta ao resumo diário

Formato da reunião:
1. Orquestrador convoca
2. CTO abre com contexto técnico
3. Rodada de status (cada departamento)
4. CEO pergunta ou decide
5. Rodada de opiniões (ordem hierárquica)
6. CTO sintetiza
7. CEO aprova/veta
8. Ata automática salva
"""
import logging
from datetime import datetime, timezone
from typing import Optional
from app.agents.registry import get_all_agents, get_agent
from app.agents.base import AgentContext, AgentOpinion, AuthorityLevel

logger = logging.getLogger(__name__)


class Council:
    """Mesa Redonda EcoZap — protocolo de reunião entre agentes."""

    def __init__(self, db_client=None, telegram_client=None):
        self.db = db_client
        self.telegram = telegram_client

    async def call_meeting(
        self,
        subject: str,
        called_by: str = "CEO",
        context: Optional[dict] = None,
    ) -> dict:
        """
        Convoca uma reunião completa. Retorna a ata.

        Passos:
        1. Convocação
        2. CTO abre
        3. Status de cada departamento
        4. Rodada de opiniões
        5. Síntese CTO
        6. Ata
        """
        ctx = AgentContext(
            triggered_by=called_by,
            payload={"subject": subject, **(context or {})}
        )
        minutes = {
            "subject": subject,
            "called_by": called_by,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "opening": None,
            "status_reports": [],
            "opinions": [],
            "synthesis": None,
            "awaiting_ceo_decision": True,
            "closed_at": None,
        }

        agents = get_all_agents()
        agents_sorted = sorted(
            [a for a in agents if a],
            key=lambda a: a.authority_level
        )

        # 1. Abertura: CTO
        cto = get_agent("cto")
        if cto:
            try:
                opening = await cto.report_status()
                minutes["opening"] = {
                    "agent": "CTO",
                    "content": opening,
                }
                logger.info(f"[Council] CTO abriu a reunião.")
            except Exception as e:
                logger.warning(f"[Council] CTO não disponível: {e}")

        # 2. Status de cada agente (ordem hierárquica)
        for agent in agents_sorted:
            if agent.role == "cto":
                continue
            try:
                status = await agent.report_status()
                minutes["status_reports"].append({
                    "agent": agent.display_name,
                    "role": agent.role,
                    "department": agent.department,
                    "status": status,
                })
            except Exception as e:
                logger.warning(f"[Council] {agent.role} não reportou: {e}")

        # 3. Rodada de opiniões (ordem hierárquica)
        # Lei 5: espaço de fala garantido — 1 turno por agente antes de 2º turno
        for agent in agents_sorted:
            try:
                opinion: AgentOpinion = agent.opine(subject, ctx)
                minutes["opinions"].append({
                    "agent": agent.display_name,
                    "role": agent.role,
                    "agrees": opinion.agrees,
                    "reasoning": opinion.reasoning,
                    "proposal": opinion.proposal,
                })
            except Exception as e:
                logger.warning(f"[Council] {agent.role} não emitiu opinião: {e}")

        # 4. Síntese CTO
        if cto:
            dissenting = [o for o in minutes["opinions"] if not o["agrees"]]
            synthesis = {
                "total_agents": len(minutes["opinions"]),
                "dissenting": len(dissenting),
                "dissenting_agents": [d["agent"] for d in dissenting],
                "message": (
                    f"Reunião sobre '{subject}'. "
                    f"{len(agents_sorted)} agentes presentes. "
                    f"{len(dissenting)} objeções. "
                    f"Aguardando decisão do CEO."
                ),
            }
            minutes["synthesis"] = synthesis
            logger.info(f"[Council] Síntese: {synthesis['message']}")

        # 5. Ata
        minutes["closed_at"] = datetime.now(timezone.utc).isoformat()
        await self._save_minutes(minutes)

        return minutes

    async def _save_minutes(self, minutes: dict) -> None:
        """Salva a ata em Supabase e notifica via Telegram."""
        # Salva em Supabase
        if self.db:
            try:
                self.db.table("council_meetings").insert({
                    "subject": minutes["subject"],
                    "called_by": minutes["called_by"],
                    "minutes": minutes,
                    "created_at": minutes["started_at"],
                }).execute()
            except Exception as e:
                logger.error(f"[Council] Erro ao salvar ata: {e}")

        # Notifica Telegram
        if self.telegram:
            summary = self._format_summary(minutes)
            try:
                await self.telegram.send(summary)
            except Exception as e:
                logger.error(f"[Council] Erro ao notificar Telegram: {e}")

    def _format_summary(self, minutes: dict) -> str:
        """Formata resumo da ata para Telegram."""
        opinions = minutes.get("opinions", [])
        agrees = sum(1 for o in opinions if o["agrees"])
        dissents = len(opinions) - agrees

        lines = [
            f"📋 *Reunião EcoZap*",
            f"Assunto: {minutes['subject']}",
            f"Convocada por: {minutes['called_by']}",
            f"",
            f"*Participantes:* {len(opinions)} agentes",
            f"✅ Alinhados: {agrees} | ⚠️ Objeções: {dissents}",
        ]

        if dissents > 0:
            dissenters = [o["agent"] for o in opinions if not o["agrees"]]
            lines.append(f"Discordaram: {', '.join(dissenters)}")

        lines.append(f"\n⏳ Aguarda decisão do CEO Joanderson.")
        return "\n".join(lines)
