import anthropic
import google.generativeai as genai
from app.config import get_settings
import logging

logger = logging.getLogger(__name__)
CLAUDE_HAIKU = "claude-haiku-4-5-20251001"
GEMINI_FLASH = "gemini-2.0-flash"
MAX_RESPONSE_TOKENS = 300

class AIService:
    def __init__(self):
        settings = get_settings()
        self.claude = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        if settings.google_api_key:
            genai.configure(api_key=settings.google_api_key)
            self.gemini = genai.GenerativeModel(GEMINI_FLASH)
        else:
            self.gemini = None

    async def respond(self, system_prompt: str, history: list, user_message: str, use_gemini: bool = False) -> str:
        if use_gemini and self.gemini:
            return await self._respond_gemini(system_prompt, history, user_message)
        return await self._respond_claude(system_prompt, history, user_message)

    async def _respond_claude(self, system_prompt: str, history: list, user_message: str) -> str:
        messages = history + [{"role": "user", "content": user_message}]
        response = self.claude.messages.create(model=CLAUDE_HAIKU, max_tokens=MAX_RESPONSE_TOKENS, system=system_prompt, messages=messages)
        return response.content[0].text.strip()

    async def _respond_gemini(self, system_prompt: str, history: list, user_message: str) -> str:
        chat_history = [{"role": "user" if m["role"]=="user" else "model", "parts": [m["content"]]} for m in history]
        chat = self.gemini.start_chat(history=chat_history)
        response = chat.send_message(f"{system_prompt}\n\nMensagem: {user_message}")
        return response.text.strip()

    async def classify_intent(self, message: str, context: str = "") -> dict:
        prompt = f"""Analise esta mensagem de WhatsApp e retorne um JSON com:
- intent: compra | suporte | agendamento | informacao | objecao | cancelamento | outros
- lead_score_delta: numero de -10 a +20
- is_simple: true se for mensagem simples (oi, obrigado, ok)
- urgency: alta | media | baixa

Contexto: {context or 'nenhum'}
Mensagem: {message}

Responda APENAS o JSON."""
        response = self.claude.messages.create(model=CLAUDE_HAIKU, max_tokens=100, messages=[{"role": "user", "content": prompt}])
        import json
        try:
            return json.loads(response.content[0].text.strip())
        except Exception:
            return {"intent": "outros", "lead_score_delta": 0, "is_simple": False, "urgency": "media"}

    async def compress_conversation(self, messages: list) -> str:
        text = "\n".join([f"{m['role']}: {m['content']}" for m in messages])
        prompt = f"Resuma esta conversa em maximo 150 palavras. Inclua: pontos discutidos, intencao do cliente, objecoes, onde ficou.\n\n{text}"
        response = self.claude.messages.create(model=CLAUDE_HAIKU, max_tokens=200, messages=[{"role": "user", "content": prompt}])
        return response.content[0].text.strip()

    async def analyze_owner_links(self, scraped_content: str) -> dict:
        prompt = f"""Analise este conteudo e extraia um JSON com:
- tone, vocabulary (lista), emoji_style, avg_response_length, values (lista)
- business_type, main_offer, target_audience
- common_objections (lista), context_summary (max 300 palavras)

Conteudo:
{scraped_content[:8000]}

Responda APENAS o JSON."""
        response = self.claude.messages.create(model="claude-sonnet-4-6", max_tokens=1000, messages=[{"role": "user", "content": prompt}])
        import json
        try:
            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("```")[1].replace("json", "").strip()
            return json.loads(text)
        except Exception as e:
            logger.error(f"Erro ao parsear analise de links: {e}")
            return {}
