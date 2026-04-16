"""
EcoZap — Message Bus
=====================
Lei 3: "Conversam entre eles." — Redis pub/sub entre agentes.

Canal: ecozap:agents:events
Formato: { "from": role, "event": tipo, "payload": {}, "ts": timestamp }
"""
import json
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger(__name__)

CHANNEL = "ecozap:agents:events"


def publish(redis_client, from_role: str, event: str, payload: dict = None) -> bool:
    """Publica um evento no bus. Todos os agentes ouvintes recebem."""
    message = {
        "from": from_role,
        "event": event,
        "payload": payload or {},
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    try:
        subscribers = redis_client.publish(CHANNEL, json.dumps(message))
        logger.info(f"[Bus] {from_role} → event={event} | {subscribers} ouvintes")
        return True
    except Exception as e:
        logger.error(f"[Bus] Falha ao publicar evento: {e}")
        return False


def subscribe(redis_client, handler: Callable[[dict], None]) -> None:
    """
    Inscreve um handler no bus de eventos.
    Chamado em thread separada (blocking).
    """
    pubsub = redis_client.pubsub()
    pubsub.subscribe(CHANNEL)
    logger.info(f"[Bus] Inscrito no canal: {CHANNEL}")
    for raw_message in pubsub.listen():
        if raw_message["type"] == "message":
            try:
                message = json.loads(raw_message["data"])
                handler(message)
            except Exception as e:
                logger.error(f"[Bus] Erro ao processar mensagem: {e}")


# ─── Tipos de eventos padrão ──────────────────────────────────────────────────

class Events:
    # OPS
    ANOMALY_DETECTED   = "anomaly_detected"      # Sentinel detectou problema
    DIAGNOSIS_READY    = "diagnosis_ready"        # Doctor terminou diagnóstico
    PATCH_READY        = "patch_ready"            # Surgeon gerou patch
    PATCH_DEPLOYED     = "patch_deployed"         # Surgeon fez deploy
    INCIDENT_OPENED    = "incident_opened"        # Novo incidente
    INCIDENT_RESOLVED  = "incident_resolved"      # Incidente resolvido
    BACKUP_VALIDATED   = "backup_validated"       # Guardian validou backup
    BACKUP_SKIPPED     = "backup_skipped"         # Guardian recusou backup corrompido

    # REUNIÃO
    MEETING_CALLED     = "meeting_called"         # CEO convocou reunião
    MEETING_STATUS     = "meeting_status"         # Agente entregou status
    MEETING_OPINION    = "meeting_opinion"        # Agente emitiu opinião
    MEETING_CLOSED     = "meeting_closed"         # Reunião encerrada

    # NEGÓCIO
    LEAD_QUALIFIED     = "lead_qualified"         # Attendant qualificou lead
    SALE_CLOSED        = "sale_closed"            # Closer fechou venda
    CAMPAIGN_TRIGGERED = "campaign_triggered"     # Campanha disparada
