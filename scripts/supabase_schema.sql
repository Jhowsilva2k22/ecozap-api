-- WHATSAPP AI AGENT - Schema Supabase
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS owners (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone TEXT NOT NULL UNIQUE,
    business_name TEXT NOT NULL,
    business_type TEXT, notify_phone TEXT, evolution_instance TEXT,
    tone TEXT, vocabulary JSONB DEFAULT '[]', emoji_style TEXT,
    avg_response_length TEXT, values JSONB DEFAULT '[]',
    product_description TEXT, main_offer TEXT, price_range TEXT,
    target_audience TEXT, common_objections JSONB DEFAULT '[]',
    faqs JSONB DEFAULT '[]', context_summary TEXT,
    links_processed JSONB DEFAULT '[]', agent_mode TEXT DEFAULT 'both',
    qualification_questions JSONB DEFAULT '[]', handoff_threshold INT DEFAULT 70,
    daily_summary_time TEXT DEFAULT '20:00',
    created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone TEXT NOT NULL, owner_id UUID REFERENCES owners(id) ON DELETE CASCADE,
    name TEXT, communication_style TEXT, emoji_usage TEXT, avg_message_length TEXT,
    lead_score INT DEFAULT 0, lead_status TEXT DEFAULT 'novo',
    intent TEXT, last_intent TEXT, summary TEXT,
    objections JSONB DEFAULT '[]', interests JSONB DEFAULT '[]',
    total_messages INT DEFAULT 0,
    first_contact TIMESTAMPTZ DEFAULT NOW(), last_contact TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE(phone, owner_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone TEXT NOT NULL, owner_id UUID REFERENCES owners(id) ON DELETE CASCADE,
    role TEXT NOT NULL, content TEXT NOT NULL,
    intent_detected TEXT, lead_score_delta INT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_phone_owner ON messages(phone, owner_id, created_at DESC);

CREATE TABLE IF NOT EXISTS learnings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID REFERENCES owners(id) ON DELETE CASCADE,
    date DATE NOT NULL, data JSONB,
    hot_leads_count INT DEFAULT 0, total_conversations INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Banco de Conhecimento do Atendente ───────────────────────────────────────
-- Cada item é uma unidade de conhecimento treinada pelo dono ou extraída
-- automaticamente das conversas. O atendente consulta antes de responder.
CREATE TABLE IF NOT EXISTS knowledge_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID REFERENCES owners(id) ON DELETE CASCADE,
    -- Categoria: produto | faq | objecao | estilo | expertise | concorrente | depoimento | processo | aprendizado
    category TEXT NOT NULL DEFAULT 'faq',
    -- Conteúdo da informação (máx ~500 chars por item — autocontido)
    content TEXT NOT NULL,
    -- Origem: owner_whatsapp | nightly_learning | url_ingestao | batch
    source TEXT DEFAULT 'manual',
    -- Confiança de 0.0 a 1.0 (1.0 = direto do dono, 0.75 = aprendizado automático)
    confidence FLOAT DEFAULT 1.0,
    -- Quantas vezes o atendente usou essa informação
    times_used INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_owner_category
    ON knowledge_items(owner_id, category);

CREATE INDEX IF NOT EXISTS idx_knowledge_owner_confidence
    ON knowledge_items(owner_id, confidence DESC);

-- RPC para incrementar o contador de uso de forma atômica
CREATE OR REPLACE FUNCTION increment_knowledge_usage(item_id UUID)
RETURNS VOID AS $$
BEGIN
    UPDATE knowledge_items
    SET times_used = times_used + 1
    WHERE id = item_id;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION update_updated_at() RETURNS TRIGGER AS $$ BEGIN NEW.updated_at = NOW(); RETURN NEW; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER owners_updated_at BEFORE UPDATE ON owners FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ============================================================
-- SPRINT 5 — Multi-tenant & Billing
-- ============================================================

-- Planos disponíveis
CREATE TABLE IF NOT EXISTS public.plans (
    id           TEXT PRIMARY KEY,                 -- 'starter' | 'pro' | 'enterprise'
    name         TEXT NOT NULL,
    price_monthly DECIMAL(10,2) NOT NULL DEFAULT 0,
    msg_limit    INT  NOT NULL DEFAULT 500,        -- mensagens/mês; -1 = ilimitado
    agent_limit  INT  NOT NULL DEFAULT 1,          -- agentes ativos
    features     JSONB NOT NULL DEFAULT '[]',
    is_active    BOOL NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Seed dos planos padrão
INSERT INTO public.plans (id, name, price_monthly, msg_limit, agent_limit, features) VALUES
  ('starter',    'Starter',    97.00,   1000,  2, '["atendente","sdr","painel_leads"]'),
  ('pro',        'Pro',        197.00,  5000,  5, '["atendente","sdr","closer","consultor","trainer","knowledge_bank","painel_leads","painel_knowledge"]'),
  ('enterprise', 'Enterprise', 397.00, -1,    -1, '["todos","api_acesso","suporte_prioritario","onboarding_dedicado"]')
ON CONFLICT (id) DO NOTHING;

-- Colunas de billing na tabela owners
ALTER TABLE public.owners
    ADD COLUMN IF NOT EXISTS plan_id            TEXT REFERENCES public.plans(id) DEFAULT 'starter',
    ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT,
    ADD COLUMN IF NOT EXISTS stripe_sub_id      TEXT,
    ADD COLUMN IF NOT EXISTS sub_status         TEXT DEFAULT 'trial',   -- trial | active | past_due | canceled
    ADD COLUMN IF NOT EXISTS trial_ends_at      TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '14 days'),
    ADD COLUMN IF NOT EXISTS sub_ends_at        TIMESTAMPTZ;

-- Assinaturas (histórico e eventos Stripe)
CREATE TABLE IF NOT EXISTS public.subscriptions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id        UUID NOT NULL REFERENCES public.owners(id) ON DELETE CASCADE,
    plan_id         TEXT NOT NULL REFERENCES public.plans(id),
    stripe_sub_id   TEXT,
    stripe_invoice  TEXT,
    status          TEXT NOT NULL DEFAULT 'active',  -- active | canceled | past_due | unpaid
    period_start    TIMESTAMPTZ,
    period_end      TIMESTAMPTZ,
    amount_paid     DECIMAL(10,2) DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    canceled_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_owner ON public.subscriptions(owner_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_stripe ON public.subscriptions(stripe_sub_id);

-- Logs de uso mensal por dono
CREATE TABLE IF NOT EXISTS public.usage_logs (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id       UUID NOT NULL REFERENCES public.owners(id) ON DELETE CASCADE,
    month          TEXT NOT NULL,  -- 'YYYY-MM'
    messages_count INT  NOT NULL DEFAULT 0,
    agents_used    INT  NOT NULL DEFAULT 0,
    updated_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(owner_id, month)
);
CREATE INDEX IF NOT EXISTS idx_usage_owner_month ON public.usage_logs(owner_id, month);

-- RPC: incrementar contador de mensagens
CREATE OR REPLACE FUNCTION increment_usage(p_owner_id UUID, p_month TEXT)
RETURNS INT LANGUAGE plpgsql AS $$
DECLARE v_count INT;
BEGIN
    INSERT INTO public.usage_logs (owner_id, month, messages_count)
    VALUES (p_owner_id, p_month, 1)
    ON CONFLICT (owner_id, month)
    DO UPDATE SET messages_count = usage_logs.messages_count + 1,
                  updated_at = NOW()
    RETURNING messages_count INTO v_count;
    RETURN v_count;
END;
$$;

-- RPC: checar se owner ainda está dentro do limite
CREATE OR REPLACE FUNCTION check_usage_limit(p_owner_id UUID)
RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
    v_owner    RECORD;
    v_plan     RECORD;
    v_used     INT;
    v_month    TEXT;
BEGIN
    v_month := TO_CHAR(NOW(), 'YYYY-MM');

    SELECT o.plan_id, o.sub_status, o.trial_ends_at, o.sub_ends_at
    INTO v_owner FROM public.owners o WHERE o.id = p_owner_id;

    SELECT p.msg_limit INTO v_plan FROM public.plans p WHERE p.id = v_owner.plan_id;

    SELECT COALESCE(messages_count, 0) INTO v_used
    FROM public.usage_logs
    WHERE owner_id = p_owner_id AND month = v_month;

    -- -1 = ilimitado
    IF v_plan.msg_limit = -1 THEN
        RETURN jsonb_build_object('allowed', TRUE, 'used', v_used, 'limit', -1, 'status', v_owner.sub_status);
    END IF;

    RETURN jsonb_build_object(
        'allowed',  (v_used < v_plan.msg_limit) AND (v_owner.sub_status IN ('active','trial')),
        'used',     v_used,
        'limit',    v_plan.msg_limit,
        'status',   v_owner.sub_status
    );
END;
$$;
