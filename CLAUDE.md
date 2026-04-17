# EcoZap — Contexto para Claude Code
> Lido automaticamente pelo terminal Claude Code ao entrar nesta pasta.
> Atualizado a cada sprint. Última atualização: 2026-04-17 | Commit: `cd4feac3`

---

## O QUE É ESTE PROJETO

**EcoZap** — SaaS multi-tenant de atendimento via WhatsApp com força de vendas autônoma.
Um time de agentes de IA trabalha em paralelo: atende leads, qualifica, fecha vendas,
retém clientes, aprende todo dia e se auto-corrige quando algo dá errado.

- **Stack:** FastAPI + Celery + Redis (Railway) | Supabase | Claude Sonnet/Haiku | Evolution API
- **Repo:** `Jhowsilva2k22/ecozap-api` (GitHub — já renomeado ✅)
- **Deploy:** Railway prod | **Alertas:** Telegram (CEO)

---

## ESTADO ATUAL — Sprint 5 + Fix Multi-tenant ✅

### O que está funcionando em produção
- Sentinel → Doctor → Surgeon (pipeline de auto-correção a cada 5 min via Celery)
- Guardian (valida backups antes de salvar no Supabase)
- CEO Override via Telegram (APROVADO:id / REJEITADO:id)
- SDR + Closer + Consultant com roteamento automático via AgentService
- QualifierAgent com prompt humanizado (NUNCA INVENTA, NUNCA REVELA)
- Knowledge Bank — banco de conhecimento treinável por owner
- Nightly Learning → alimenta Knowledge Bank automaticamente
- Trainer — owner treina o bot via WhatsApp (/treinar /conhecimento /esquecer)
- Painel Web: Leads (❄🌡🔥) + Knowledge Bank UI + **Billing UI**
- **Billing Asaas: PIX + Boleto + Cartão + Recorrência**
- **Planos: Starter R$97 (1k msgs) / Pro R$197 (5k msgs) / Enterprise R$397 (ilimitado)**
- **Limites de uso verificados a cada mensagem (BillingMiddleware)**
- **Multi-tenant `evolution_instance`: cada owner usa sua própria instância WhatsApp ✅**

### Commits importantes
| Commit | O que fez |
|---|---|
| `8018111` | Knowledge Bank + Trainer + SDR relacional + Nightly Learning |
| `cf3e3cd` | CLAUDE.md — contexto automático para terminal |
| `e964dca` | Trainer no webhook + fix nurture_customers |
| `77ff306` | Painel Knowledge Bank + temperatura visual + navegação |
| `cd794c1` | Checkpoint Sprint 4 |
| `e43e91b` | **Sprint 5: Billing Asaas PIX/Boleto/Cartão + planos + limites** |
| `b793f16b` | **fix: WhatsAppService.send_message usa instance por owner (não env fixo)** |
| `9a5cc610` | **fix: sender.py repassa instance em send_message/send_typing/download_media** |
| `b293a2be` | **fix: qualifier.py extrai evolution_instance do owner e passa em todos os sends** |
| `1fd767de` | **fix: webhook.py usa evolution_instance do owner em todos os comandos** |
| `cd4feac3` | **fix: tasks.py — follow_up_active/cold_leads/nurture_customers corrigidos** |

---

## BUG MULTI-TENANT — RESOLVIDO ✅

**Problema:** toda a cadeia de envio usava `settings.evolution_instance` (variável de ambiente global),
enviando respostas para a instância errada quando múltiplos owners existiam.

**Correção aplicada em 5 arquivos:**

| Arquivo | Fix |
|---|---|
| `app/services/whatsapp.py` | `send_message/send_typing/download_media_base64` aceitam `instance=None`; usa `_instance()` helper |
| `app/services/sender.py` | Repassa `instance` para WhatsAppService em todas as funções |
| `app/agents/qualifier.py` | Extrai `evolution_instance = owner.get("evolution_instance")` e passa em todos os sends |
| `app/routers/webhook.py` | Todos os handlers de comando usam `evolution_instance` do owner |
| `app/queues/tasks.py` | `follow_up_active` busca instance no DB; `follow_up_cold_leads` e `nurture_customers` incluem `evolution_instance` no SELECT e corrigem ordem dos args do `send_message` |

**Padrão correto em qualquer novo código:**
```python
owner = await memory.get_owner_context(owner_id)
evolution_instance = owner.get("evolution_instance") or ""
await whatsapp.send_message(phone, text, instance=evolution_instance)
```

---

## PRÓXIMO SPRINT (Sprint 6 — sugestão)

1. **Onboarding multi-tenant:** formulário público `/cadastro` → cria owner → conecta WhatsApp
2. **Dashboard admin:** visão geral de todos os owners (mrr, churn, uso)
3. **Smoke tests end-to-end:** lead → SDR → Closer → Consultant → Painel

---

## ARQUIVOS CRÍTICOS (leia antes de mexer)

```
app/agents/qualifier.py          Motor do atendente humanizado
app/agents/business/sdr.py       Qualificação relacional + temperatura ❄🌡🔥
app/agents/business/closer.py    Objeções/compra + fechamento
app/agents/business/consultant.py  Retenção + upsell + onboarding
app/agents/business/trainer.py   Treinamento via WhatsApp
app/services/knowledge.py        Banco de conhecimento estruturado
app/services/agent.py            Roteador central SDR/Closer/Consultant
app/services/whatsapp.py         Gateway Evolution API — sempre passar instance=
app/services/sender.py           Roteador de canal — sempre passar instance=
app/services/learning.py         Análise noturna → alimenta KB
app/middleware/billing.py        Limite de uso por plano (checa a cada msg)
app/models/plans.py              Starter / Pro / Enterprise
app/routers/billing.py           Asaas: checkout, webhook, cancelamento
app/routers/panel.py             Leads + Knowledge + Billing UIs
app/routers/webhook.py           Entrada de mensagens WhatsApp
app/queues/tasks.py              Celery tasks — beat schedule + follow-up
app/agents/ops/sentinel.py       Monitoramento de saúde (4 checks)
scripts/supabase_schema.sql      Schema completo (plans, subscriptions, usage_logs)
```

---

## ARQUITETURA EM 30 SEGUNDOS

```
WhatsApp → webhook.py → BillingMiddleware (checa limite do plano)
                      ↓ (se permitido)
                AgentService
   ├── score 0-49   → SDR  (❄ conquista, 🌡 educa)
   ├── score 50+    → Closer (🔥 facilita o fechamento)
   └── status=cliente → Consultant (retém, faz upsell)
          ↓
   QualifierAgent (motor de resposta)
   ├── KnowledgeBank.get_context_for_prompt()
   └── build_qualifier_prompt()
          ↓
   Celery Beat (5 min): Sentinel → Doctor → Surgeon → PR → CEO aprova

Billing (Asaas — PIX/Boleto/Cartão):
   /billing/checkout → cria assinatura recorrente
   /billing/asaas-webhook → PAYMENT_RECEIVED | PAYMENT_OVERDUE | SUBSCRIPTION_DELETED
   /panel/billing → UI de planos + uso + histórico
```

---

## VARIÁVEIS DE AMBIENTE NECESSÁRIAS (Sprint 5)

```
ASAAS_API_KEY=          # $aact_... (produção) no Railway
ASAAS_ENVIRONMENT=production
ASAAS_WEBHOOK_TOKEN=    # opcional — mesmo token configurado no painel Asaas
```

---

## REGRAS QUE NUNCA MUDAM (não quebre estas)

1. **NUNCA INVENTA** — se não sabe, diz "deixa eu verificar"
2. **NUNCA REVELA** — nunca confirma ser IA, sistema, backend ou bastidores
3. **SDR nunca empurra venda** — relacionamento primeiro, venda é consequência
4. **CEO Override** — toda ação crítica (merge, deploy, delete) passa pelo Telegram
5. **Knowledge Bank** — memória permanente; tudo que aprende vai para lá
6. **Multi-tenant** — SEMPRE usar `evolution_instance` do owner, nunca `settings.evolution_instance`
7. **Checkpoint** — atualizar `CLAUDE.md` + `1-COMECE-AQUI.md` + Notion a cada sprint

---

## PENDÊNCIAS MANUAIS (sem código)

| Ação | Onde |
|---|---|
| Aplicar migration Sprint 5 | Supabase → SQL Editor → cole a seção "SPRINT 5" do `scripts/supabase_schema.sql` |
| Renomear projeto Supabase `axtor_comercial 1` → `ecozap` | Supabase → Settings → General |
| Adicionar ASAAS_API_KEY no Railway | Railway → Variables |
| Configurar webhook no Asaas | Painel Asaas → Configurações → Webhooks → URL: `seu-app/billing/asaas-webhook` |
| **Testar bot axtor-labs no WhatsApp** | Mandar mensagem e confirmar resposta via instância correta |

---

## COMO RETOMAR QUALQUER SESSÃO

Se você é uma nova instância do Claude lendo isto:
1. Leia este arquivo — você já sabe tudo do projeto
2. Rode `git log --oneline -10` para ver os últimos commits
3. O próximo passo está na seção **PRÓXIMO SPRINT** acima
4. Nunca reescreva o que já está feito — só avance
5. **Multi-tenant:** qualquer novo send de WhatsApp DEVE passar `instance=evolution_instance` do owner
