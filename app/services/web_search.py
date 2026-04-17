"""
EcoZap — Web Search Service (Option A)
=======================================
Busca notícias e tendências via Brave Search API, resume com Claude Haiku
e salva no KnowledgeBank do tenant como aprendizados diários.

Fluxo:
  1. Recebe lista de tópicos (ou usa DEFAULT_TOPICS)
  2. Busca cada tópico na Brave Search (últimos 7 dias, pt-BR)
  3. Resume top resultados com Claude Haiku (barato e rápido)
  4. Salva no knowledge_items com category="aprendizado"

Requisito: BRAVE_API_KEY no Railway env
  → https://api.search.brave.com (plano Free: 2.000 buscas/mês)
"""

import logging
from typing import Optional

import requests

from app.config import get_settings
from app.services.knowledge import KnowledgeBank

logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Tópicos padrão — genéricos, aplicáveis a qualquer negócio no WhatsApp
# ---------------------------------------------------------------------------
DEFAULT_TOPICS = [
    "tendências vendas WhatsApp Business 2025 Brasil",
    "estratégias atendimento ao cliente automatizado IA",
    "marketing digital pequenas empresas brasileiras",
    "copywriting persuasivo WhatsApp conversão vendas",
    "educação financeira empreendedores pequenos negócios",
]


class WebSearchService:
    """
    Serviço de busca autônoma na web.
    Síncrono — projetado para uso dentro de tasks Celery.
    """

    def __init__(self):
        self.brave_key = settings.brave_api_key
        self.anthropic_key = settings.anthropic_api_key

    # ──────────────────────── busca ────────────────────────

    def _search_brave(self, query: str, count: int = 5) -> list[dict]:
        """
        Chama Brave Search API e retorna lista de resultados.
        Retorna [] se brave_api_key não estiver configurada.
        """
        if not self.brave_key:
            logger.warning(
                "[WebSearch] BRAVE_API_KEY não configurada — configure no Railway env"
            )
            return []

        url = "https://api.search.brave.com/res/v1/web/search"
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self.brave_key,
        }
        params = {
            "q": query,
            "count": min(count, 10),
            "freshness": "pw",          # past week — conteúdo fresco
            "text_decorations": False,
            "search_lang": "pt",
            "country": "BR",
            "safesearch": "moderate",
        }

        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            raw = data.get("web", {}).get("results", [])
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "description": r.get("description", "") or r.get("extra_snippets", [""])[0],
                }
                for r in raw[:count]
                if r.get("description") or r.get("extra_snippets")
            ]
        except requests.exceptions.HTTPError as e:
            logger.error("[WebSearch] HTTP %s ao buscar '%s': %s", e.response.status_code, query, e)
        except Exception as e:
            logger.error("[WebSearch] Erro ao buscar '%s': %s", query, e)

        return []

    # ──────────────────────── sumarização ────────────────────────

    def _summarize(self, topic: str, results: list[dict]) -> Optional[str]:
        """
        Usa Claude Haiku para sintetizar os resultados em insights práticos.
        Retorna None se não houver conteúdo ou se Claude falhar.
        """
        snippets = "\n".join(
            f"- {r['title']}: {r['description']}"
            for r in results
            if r.get("description")
        ).strip()

        if not snippets:
            return None

        prompt = (
            f"Você é um analista de negócios brasileiro. Analise os resultados de busca abaixo "
            f"sobre o tema: '{topic}'.\n\n"
            f"Extraia 2-3 insights práticos e aplicáveis para pequenas empresas que vendem via WhatsApp.\n\n"
            f"Resultados:\n{snippets}\n\n"
            f"Regras:\n"
            f"- Escreva em português direto, sem enrolação\n"
            f"- Máximo 3 bullets curtos (1-2 linhas cada)\n"
            f"- Só insights que um vendedor pode usar hoje\n"
            f"- Nada genérico, nada óbvio"
        )

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.anthropic_key)
            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text.strip()
        except Exception as e:
            logger.error("[WebSearch] Erro ao sumarizar com Claude Haiku: %s", e)
            return None

    # ──────────────────────── entrada principal ────────────────────────

    def search_and_learn(
        self,
        owner_id: str,
        topics: Optional[list[str]] = None,
    ) -> int:
        """
        Executa o ciclo completo: busca → resume → salva no KnowledgeBank.
        Retorna o número de insights salvos.
        """
        kb = KnowledgeBank()
        topics_to_search = topics or DEFAULT_TOPICS
        saved = 0

        logger.info(
            "[WebSearch] Iniciando busca para tenant %s — %d tópicos",
            owner_id[:8],
            len(topics_to_search),
        )

        for topic in topics_to_search:
            try:
                results = self._search_brave(topic, count=5)
                if not results:
                    logger.debug("[WebSearch] Sem resultados para '%s'", topic)
                    continue

                summary = self._summarize(topic, results)
                if not summary:
                    continue

                content = f"[Tendência: {topic}]\n{summary}"
                source_urls = " | ".join(
                    r["url"] for r in results[:3] if r.get("url")
                )

                result = kb.add_item(
                    owner_id=owner_id,
                    category="aprendizado",
                    content=content,
                    source=f"web_search | {source_urls[:180]}",
                    confidence=0.7,
                )

                if result.get("ok"):
                    saved += 1
                    logger.info("[WebSearch] ✓ Salvo: '%s'", topic[:60])
                else:
                    reason = result.get("reason", "")
                    if reason != "duplicate":
                        logger.warning("[WebSearch] Não salvo ('%s'): %s", topic[:40], reason)

            except Exception as e:
                logger.error("[WebSearch] Erro no tópico '%s': %s", topic, e)

        logger.info(
            "[WebSearch] Concluído para tenant %s — %d/%d insights salvos",
            owner_id[:8],
            saved,
            len(topics_to_search),
        )
        return saved
