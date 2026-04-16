from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from app.config import Settings
from app.services.calendar import GoogleCalendarService
from app.routers import webhook
import httpx
from supabase import create_client

app = FastAPI(title="WhatsApp Agent")
app.include_router(webhook.router)

settings = Settings()


@app.get("/auth/google/callback")
async def google_oauth_callback(request: Request):
    """Handle Google OAuth callback."""
    try:
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        error = request.query_params.get("error")

        if error:
            return HTMLResponse("<h1>Erro na autenticação</h1><p>Por favor, tente novamente.</p>", status_code=400)

        if not code or not state:
            return HTMLResponse("<h1>Parâmetros inválidos</h1>", status_code=400)

        # Exchange code for tokens
        calendar_service = GoogleCalendarService(settings.google_client_id, settings.google_client_secret)
        token_response = await calendar_service.exchange_code(
            code,
            f"{settings.whatsapp_api_url}/auth/google/callback"
        )

        access_token = token_response.get("access_token")
        refresh_token = token_response.get("refresh_token")

        # Get user email
        user_email = await calendar_service.get_user_email(access_token)

        # Save tokens to Supabase
        supabase = create_client(settings.supabase_url, settings.supabase_service_key)
        supabase.table("owners").update({
            "google_access_token": access_token,
            "google_refresh_token": refresh_token,
            "google_email": user_email
        }).eq("id", state).execute()

        # Notify owner via WhatsApp
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{settings.whatsapp_api_url}/messages",
                json={
                    "to": state,
                    "text": "✅ Google Calendar e Gmail conectados com sucesso!"
                }
            )

        return HTMLResponse("<h1>✅ Conexão realizada!</h1><p>Você pode fechar esta janela.</p>")

    except Exception as e:
        print(f"OAuth callback error: {e}")
        return HTMLResponse(f"<h1>Erro</h1><p>{str(e)}</p>", status_code=500)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}
