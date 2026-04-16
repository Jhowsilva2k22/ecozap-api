import json
import redis
from datetime import datetime
from typing import Optional, Dict, Any, List
from zoneinfo import ZoneInfo
from app.services.calendar import GoogleCalendarService
from app.models.owner import OwnerProfile
from app.models.customer import CustomerProfile


class AttendantAgent:
    """WhatsApp attendant agent with scheduling capabilities."""

    def __init__(
        self,
        redis_url: str,
        calendar_service: GoogleCalendarService,
        owner: OwnerProfile,
        customer: CustomerProfile
    ):
        self.redis_client = redis.from_url(redis_url)
        self.calendar_service = calendar_service
        self.owner = owner
        self.customer = customer
        self.brt = ZoneInfo("America/Sao_Paulo")

    async def process(self, message: str) -> str:
        """Process incoming message and return response."""
        # Check if we're in a scheduling flow
        if await self._sched_handle_step(message):
            return await self._get_sched_response()

        # Classify intent
        intent = self._classify_intent(message)

        if intent == "schedule":
            await self._sched_start_flow()
            return await self._get_sched_response()

        return self._generate_response(intent, message)

    def _classify_intent(self, message: str) -> str:
        """Classify user intent from message."""
        message_lower = message.lower()
        if any(word in message_lower for word in ["agendar", "marcar", "horario", "quando"]):
            return "schedule"
        return "general"

    async def _sched_state_get(self) -> Optional[Dict[str, Any]]:
        """Get current scheduling state from Redis."""
        state_key = f"sched:{self.owner.id}:{self.customer.id}"
        state_json = self.redis_client.get(state_key)
        if not state_json:
            return None
        return json.loads(state_json)

    async def _sched_state_set(self, state: Dict[str, Any]) -> None:
        """Save scheduling state to Redis with TTL."""
        state_key = f"sched:{self.owner.id}:{self.customer.id}"
        self.redis_client.setex(state_key, 1800, json.dumps(state))

    async def _sched_state_clear(self) -> None:
        """Clear scheduling state from Redis."""
        state_key = f"sched:{self.owner.id}:{self.customer.id}"
        self.redis_client.delete(state_key)

    def _parse_slot_choice(self, message: str) -> Optional[int]:
        """Parse user's time slot selection from message."""
        message_lower = message.lower().strip()
        if message_lower.isdigit():
            return int(message_lower)
        if "primeiro" in message_lower or "1" in message_lower:
            return 1
        if "segundo" in message_lower or "2" in message_lower:
            return 2
        if "terceiro" in message_lower or "3" in message_lower:
            return 3
        return None

    async def _sched_start_flow(self) -> None:
        """Initiate scheduling flow."""
        if not self.owner.google_access_token:
            await self._sched_state_clear()
            return

        today = datetime.now(self.brt).strftime("%Y-%m-%d")
        free_slots = await self.calendar_service.get_free_slots(
            self.owner.google_access_token,
            self.owner.google_calendar_id,
            today
        )

        state = {
            "step": "offering_slots",
            "slots": free_slots,
            "created_at": datetime.now(self.brt).isoformat()
        }
        await self._sched_state_set(state)

    async def _sched_handle_step(self, message: str) -> bool:
        """Handle scheduling flow step. Returns True if scheduling is in progress."""
        state = await self._sched_state_get()
        if not state:
            return False

        current_step = state.get("step")

        if current_step == "offering_slots":
            slot_choice = self._parse_slot_choice(message)
            if slot_choice and slot_choice <= len(state.get("slots", [])):
                state["step"] = "awaiting_email"
                state["selected_slot_idx"] = slot_choice - 1
                await self._sched_state_set(state)
                return True

        elif current_step == "awaiting_email":
            if "@" in message:
                state["step"] = "awaiting_confirmation"
                state["customer_email"] = message.strip()
                await self._sched_state_set(state)
                return True

        elif current_step == "awaiting_confirmation":
            if message.lower() in ["sim", "yes", "confirmar"]:
                await self._sched_create_and_confirm(state)
                await self._sched_state_clear()
                return True

        return True

    async def _get_sched_response(self) -> str:
        """Generate response based on current scheduling state."""
        state = await self._sched_state_get()
        if not state:
            return "Agendamento cancelado."

        step = state.get("step")

        if step == "offering_slots":
            slots = state.get("slots", [])
            if not slots:
                await self._sched_state_clear()
                return "Desculpe, não há horários disponíveis hoje."
            response = "Horários disponíveis:\n"
            for i, slot in enumerate(slots[:3], 1):
                response += f"{i}. {slot['start']}\n"
            response += "\nDigite o número do horário desejado."
            return response

        elif step == "awaiting_email":
            return "Qual é o seu email para confirmar o agendamento?"

        elif step == "awaiting_confirmation":
            slot_idx = state.get("selected_slot_idx", 0)
            slots = state.get("slots", [])
            if slot_idx < len(slots):
                slot = slots[slot_idx]
                return f"Confirmar agendamento para {slot['start']}? (Responda com 'sim' ou 'não')"
            return "Erro ao processar agendamento."

        return "Agendamento em andamento."

    async def _sched_create_and_confirm(self, state: Dict[str, Any]) -> None:
        """Create calendar event and send confirmation email."""
        try:
            slot_idx = state.get("selected_slot_idx", 0)
            slots = state.get("slots", [])
            customer_email = state.get("customer_email")

            if slot_idx >= len(slots) or not customer_email:
                return

            slot = slots[slot_idx]
            event = await self.calendar_service.create_event_with_meet(
                self.owner.google_access_token,
                self.owner.google_calendar_id,
                f"Reunião com {self.customer.name}",
                slot["start_iso"],
                slot["end_iso"],
                customer_email
            )

            meet_link = event.get("conferenceData", {}).get("entryPoints", [{}])[0].get("uri", "")
            await self.calendar_service.send_confirmation_email(
                self.owner.google_access_token,
                customer_email,
                "Agendamento Confirmado",
                f"Sua reunião foi agendada para {slot['start']}.\nLink Google Meet: {meet_link}"
            )
        except Exception as e:
            print(f"Error creating event: {e}")

    def _generate_response(self, intent: str, message: str) -> str:
        """Generate response based on intent."""
        if intent == "general":
            return "Olá! Como posso ajudá-lo?"
        return "Desculpe, não entendi sua solicitação."
