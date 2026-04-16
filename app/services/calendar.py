import httpx
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from zoneinfo import ZoneInfo


class GoogleCalendarService:
    """Google Calendar + Gmail integration service for scheduling."""

    GOOGLE_OAUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
    GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3"
    GOOGLE_GMAIL_API = "https://www.googleapis.com/gmail/v1"
    GOOGLE_OAUTH_USERINFO = "https://www.googleapis.com/oauth2/v1/userinfo"

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret

    def build_oauth_url(self, redirect_uri: str, state: str) -> str:
        """Build Google OAuth authorization URL."""
        scopes = "https://www.googleapis.com/auth/calendar https://www.googleapis.com/auth/gmail.send"
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scopes,
            "state": state,
            "access_type": "offline",
            "prompt": "consent"
        }
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        return f"{self.GOOGLE_OAUTH_URL}?{query_string}"

    async def exchange_code(self, code: str, redirect_uri: str) -> Dict[str, Any]:
        """Exchange authorization code for access and refresh tokens."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.GOOGLE_TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri
                }
            )
            return response.json()

    async def get_user_email(self, access_token: str) -> str:
        """Get authenticated user's email from Google."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                self.GOOGLE_OAUTH_USERINFO,
                headers={"Authorization": f"Bearer {access_token}"}
            )
            data = response.json()
            return data.get("email", "")

    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh expired access token using refresh token."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.GOOGLE_TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token"
                }
            )
            return response.json()

    async def get_free_slots(
        self,
        access_token: str,
        calendar_id: str,
        date: str,
        duration_minutes: int = 60
    ) -> List[Dict[str, str]]:
        """Get free time slots on a specific date."""
        brt = ZoneInfo("America/Sao_Paulo")
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
        start_of_day = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=brt)
        end_of_day = datetime.combine(target_date, datetime.max.time()).replace(tzinfo=brt)

        # Working hours: 8 AM to 6 PM, Monday to Friday
        if start_of_day.weekday() >= 5:  # Saturday or Sunday
            return []

        start_of_day = start_of_day.replace(hour=8, minute=0, second=0, microsecond=0)
        end_of_day = end_of_day.replace(hour=18, minute=0, second=0, microsecond=0)

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.GOOGLE_CALENDAR_API}/calendars/{calendar_id}/freebusy",
                headers={"Authorization": f"Bearer {access_token}"},
                json={
                    "timeMin": start_of_day.isoformat(),
                    "timeMax": end_of_day.isoformat(),
                    "items": [{"id": calendar_id}]
                }
            )
            busy_data = response.json()

        # Extract busy periods
        busy_periods = busy_data.get("calendars", {}).get(calendar_id, {}).get("busy", [])

        # Find free slots
        free_slots = []
        current = start_of_day

        while current.time() < end_of_day.time():
            slot_end = current + timedelta(minutes=duration_minutes)

            # Check if slot overlaps with any busy period
            is_busy = False
            for busy in busy_periods:
                busy_start = datetime.fromisoformat(busy["start"]).astimezone(brt)
                busy_end = datetime.fromisoformat(busy["end"]).astimezone(brt)
                if current < busy_end and slot_end > busy_start:
                    is_busy = True
                    current = busy_end
                    break

            if not is_busy:
                free_slots.append({
                    "start": self._format_label(current),
                    "start_iso": current.isoformat(),
                    "end_iso": slot_end.isoformat()
                })
                current = slot_end

        return free_slots

    async def create_event_with_meet(
        self,
        access_token: str,
        calendar_id: str,
        title: str,
        start_iso: str,
        end_iso: str,
        attendee_email: str
    ) -> Dict[str, Any]:
        """Create a calendar event with Google Meet conference."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.GOOGLE_CALENDAR_API}/calendars/{calendar_id}/events",
                headers={"Authorization": f"Bearer {access_token}"},
                json={
                    "summary": title,
                    "start": {"dateTime": start_iso},
                    "end": {"dateTime": end_iso},
                    "conferenceData": {
                        "createRequest": {
                            "requestId": str(datetime.now().timestamp()),
                            "conferenceSolutionKey": {"type": "hangoutsMeet"}
                        }
                    },
                    "attendees": [{"email": attendee_email}],
                    "reminders": {
                        "useDefault": True
                    }
                },
                params={"conferenceDataVersion": "1"}
            )
            return response.json()

    async def send_confirmation_email(
        self,
        access_token: str,
        to_email: str,
        subject: str,
        body: str
    ) -> bool:
        """Send confirmation email via Gmail."""
        import base64

        message = f"""From: {to_email}
To: {to_email}
Subject: {subject}
Content-Type: text/plain; charset="UTF-8"

{body}"""

        message_bytes = message.encode('utf-8')
        message_b64 = base64.urlsafe_b64encode(message_bytes).decode('utf-8')

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.GOOGLE_GMAIL_API}/users/me/messages/send",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"raw": message_b64}
            )
            return response.status_code == 200

    def _format_label(self, dt: datetime) -> str:
        """Format datetime for display in BRT timezone."""
        return dt.strftime("%d/%m/%Y %H:%M")
