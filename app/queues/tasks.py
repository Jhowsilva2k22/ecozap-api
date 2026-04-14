from celery import Celery
from app.config import get_settings
import asyncio
import logging

logger = logging.getLogger(__name__)
settings = get_settings()

celery_app = Celery("whatsapp_agent", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json", accept_content=["json"], result_serializer="json",
    timezone="America/Sao_Paulo", enable_utc=True,
    task_acks_late=True, worker_prefetch_multiplier=1,
    task_routes={
        "app.queues.tasks.process_message": {"queue": "messages"},
        "app.queues.tasks.nightly_learning": {"queue": "learning"},
    }
)

def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

@celery_app.task(bind=True, max_retries=3, default_retry_delay=5, queue="messages")
def process_message(self, phone: str, owner_id: str, message: str, agent_mode: str,
                    message_id: str = "", media_type: str = "text"):
    try:
        kwargs = {"message_id": message_id, "media_type": media_type}
        if agent_mode == "qualifier":
            from app.agents.qualifier import QualifierAgent
            run_async(QualifierAgent().process(phone, owner_id, message, **kwargs))
        elif agent_mode == "attendant":
            from app.agents.attendant import AttendantAgent
            run_async(AttendantAgent().process(phone, owner_id, message, **kwargs))
        elif agent_mode == "both":
            from app.services.memory import MemoryService
            customer = run_async(MemoryService().get_or_create_customer(phone, owner_id))
            if customer.lead_status in ["cliente"]:
                from app.agents.attendant import AttendantAgent
                run_async(AttendantAgent().process(phone, owner_id, message, **kwargs))
            else:
                from app.agents.qualifier import QualifierAgent
                run_async(QualifierAgent().process(phone, owner_id, message, **kwargs))
    except Exception as exc:
        logger.error(f"Erro ao processar mensagem de {phone}: {exc}")
        raise self.retry(exc=exc)

@celery_app.task(queue="learning")
def nightly_learning(owner_id: str):
    from app.services.learning import LearningService
    run_async(LearningService().run_daily_analysis(owner_id))
