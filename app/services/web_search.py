"""
EcoZap — Web Search Service (Option A)
=======================================
Busca notícias e tendências via Brave Search API, resume com Claude Haiku
e salva no KnowledgeBank do tenant como aprendizados diários.

Fluxo:
  1. Recebe role (ou usa DEFAULT_TOPICS)
  2. Busca tópicos específicos do role na Brave Search (últimos 7 dias, pt-BR)
  3. Resume top resultados com Claude Haiku (barato e rápido)
  4. Upsert no knowledge_items: ATUALIZA se tópico já existe, CRIA se novo
     → Nunca acumula entradas obsoletas sobre o mesmo tema

Roles disponíveis: qualifier, attendant, sdr, closer, consultant, trainer, ops

Requisito: BRAVE_API_KEY no Railway env
  → https://api.search.brave.com (plano Free: 2.000 buscas/mês)
  → Com 7 roles × 2-3 tópicos ≈ 17 calls/tenant/dia — seguro até ~3 tenants
"""

import logging
from typing import Optional

import requests

from app.config import get_settings
from app.services.knowledge import KnowledgeBank

logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Tópicos por role — cada agente é maestro na sua especialidade
# ---------------------------------------------------------------------------
TOPICS_BY_ROLE: dict[str, list[str]] = {
    # Qualificador: primeiro contato, rapport, triagem
    "qualifier": [
        "técnicas qualificação leads WhatsApp abordagem inicial conversão",
        "perguntas qualificação lead comercial WhatsApp pequenas empresas",
        "rapport inicial clientes WhatsApp como criar conexão interesse",
    ],
    # Atendente: suporte, dúvidas, experiência pós-venda
    "attendant": [
        "atendimento ao cliente WhatsApp respostas rápidas humanização",
        "experiência cliente automação WhatsApp sem parecer robô",
        "gestão reclamações WhatsApp clientes insatisfeitos como lidar",
    ],
    # SDR: prospecção fria, cadência, BANT
    "sdr": [
        "prospecção ativa WhatsApp mensagem fria abordagem SDR",
        "cadência follow-up leads WhatsApp sequência mensagens",
        "qualificação BANT leads frios WhatsApp pequenas empresas",
    ],
    # Closer: fechamento, objeções, urgência
    "closer": [
        "técnicas fechamento vendas WhatsApp objeções contorno",
        "urgência escassez gatilhos persuasão WhatsApp conversão",
        "scripts fechamento vendas WhatsApp objeção de preço",
    ],
    # Consultor: retenção, onboarding, upsell
    "consultant": [
        "retenção clientes pós-venda WhatsApp sucesso do cliente",
        "upsell cross-sell clientes existentes WhatsApp estratégias",
        "onboarding clientes novos WhatsApp reduzir churn",
    ],
    # Trainer: gestão de conhecimento, treinamento, documentação
    "trainer": [
        "gestão conhecimento equipes vendas treinamento contínuo",
        "documentação processos comerciais pequenas empresas como fazer",
        "treinamento IA agentes automação WhatsApp melhores práticas",
    ],
    # Ops (Sentinel, Doctor, Surgeon, Guardian): infra, APIs, deploy
    "ops": [
        "Evolution API WhatsApp Business instabilidades erros 2026",
        "Railway deploy containers Python uptime monitoramento produção",
        "Celery Redis workers falhas troubleshooting alta disponibilidade",
    ],
}

# Label legível para marcação no conteúdo salvo no KB
ROLE_LABELS: dict[str, str] = {
    "qualifier": "Qualificação",
    "attendant": "Atendimento",
    "sdr": "SDR",
    "closer": "Closer",
    "consultant": "Consultoria",
    "trainer": "Treinamento",
    "ops": "Ops/Infra",
}

# Fallback genérico — retrocompatibilidade com chamadas sem role
DEFAULT_TOPICS = [
    "tendências vendas WhatsApp Business 2026 Brasil",
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

    def _summarize(self, topic: str, results: list[dict], role: Optional[str] = None) -> Optional[str]:
        """
        Usa Claude Haiku para sintetizar os resultados em insights práticos.
        O prompt é ajustado conforme o role para extrair insights relevantes à função.
        Retorna None se não houver conteúdo ou se Claude falhar.
        """
        snippets = "\n".join(
            f"- {r['title']}: {r['description']}"
            for r in results
            if r.get("description")
        ).strip()

        if not snippets:
            return None

        # Contexto do role para o prompt de sumarização
        role_contexts = {
            "qualifier": "um agente de qualificação de leads no WhatsApp, que precisa identificar oportunidades e criar conexão rápida",
            "attendant": "um agente de atendimento ao cliente no WhatsApp, que resolve dúvidas e mantém a satisfação",
            "sdr": "um SDR (Sales Development Rep) que prospecta e aquece leads frios no WhatsApp",
            "closer": "um closer de vendas que fecha negócios e contorna objeções no WhatsApp",
            "consultant": "um consultor de sucesso do cliente que retém e faz upsell no WhatsApp",
            "trainer": "um agente de treinamento que documenta processos e capacita equipes de vendas",
            "ops": "um engenheiro de operações que monitora infra, APIs e deploys em produção",
        }

        role_ctx = role_contexts.get(role or "", "um agente de vendas e atendimento via WhatsApp")

        prompt = (
            f"Você é {role_ctx}.\n\n"
            f"Analise os resultados de busca abaixo sobre: '{topic}'.\n\n"
            f"Extraia 2-3 insights práticos e aplicáveis ESPECIFICAMENTE para a sua função.\n\n"
            f"Resultados:\n{snippets}\n\n"
            f"Regras:\n"
            f"- Escreva em português direto, sem enrolação\n"
            f"- Máximo 3 bullets curtos (1-2 linhas cada)\n"
            f"- Só insights que você pode usar hoje na sua função\n"
            f"- Nada genérico, nada óbvio\n"
            f"- Foque no que é relevante para: {role_ctx}"
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
        role: Optional[str] = None,
    ) -> int:
        """
        Executa o ciclo completo: busca → resume → upsert no KnowledgeBank.

        Prioridade de tópicos:
          1. topics= explícito (override manual)
          2. role= (usa TOPICS_BY_ROLE[role]) → agente maestro da sua função
          3. DEFAULT_TOPICS (fallback genérico)

        O conteúdo salvo é marcado com o role:
          [SDR: prospecção ativa WhatsApp...]  → identificável no KB
          [Closer: técnicas fechamento...]

        O source inclui o role:
          'web_search:closer | https://...'
        → o upsert nunca confunde tópicos de roles diferentes.

        Usa upsert_topic_item() em vez de add_item():
        - Se o tópico já existir → ATUALIZA conteúdo e data
        - Se não existir → CRIA nova entrada
        Mantém o banco sempre com exatamente 1 entrada por tópico por role.

        Retorna o número de tópicos salvos/atualizados.
        """
        kb = KnowledgeBank()

        # Resolve tópicos
        if topics:
            topics_to_search = topics
        elif role and role in TOPICS_BY_ROLE:
            topics_to_search = TOPICS_BY_ROLE[role]
        else:
            topics_to_search = DEFAULT_TOPICS

        # Label do role para marcação no conteúdo
        role_label = ROLE_LABELS.get(role or "", "Tendência")
        # Prefixo de source com role para evitar colisão no upsert
        source_prefix = f"web_search:{role}" if role else "web_search"

        saved = 0

        logger.info(
            "[WebSearch] Iniciando busca role=%s para tenant %s — %d tópicos",
            role or "default",
            owner_id[:8],
            len(topics_to_search),
        )

        for topic in topics_to_search:
            try:
                results = self._search_brave(topic, count=5)
                if not results:
                    logger.debug("[WebSearch] Sem resultados para '%s'", topic)
                    continue

                summary = self._summarize(topic, results, role=role)
                if not summary:
                    continue

                content = f"[{role_label}: {topic}]\n{summary}"
                source_urls = " | ".join(
                    r["url"] for r in results[:3] if r.get("url")
                )

                result = kb.upsert_topic_item(
                    owner_id=owner_id,
                    topic=topic,
                    content=content,
                    source=f"{source_prefix} | {source_urls[:180]}",
                    confidence=0.7,
                )

                if result.get("ok"):
                    saved += 1
                    action = result.get("action", "salvo")
                    logger.info(
                        "[WebSearch] ✓ %s [%s]: '%s'",
                        "Atualizado" if action == "updated" else "Criado",
                        role_label,
                        topic[:60],
                    )
                else:
                    logger.warning(
                        "[WebSearch] Não salvo ('%s'): %s",
                        topic[:40],
                        result.get("reason", "?"),
                    )

            except Exception as e:
                logger.error("[WebSearch] Erro no tópico '%s': %s", topic, e)

        logger.info(
            "[WebSearch] Concluído role=%s tenant %s — %d/%d tópicos processados",
            role or "default",
            owner_id[:8],
            saved,
            len(topics_to_search),
        )
        return saved
