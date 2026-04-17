"""
Planos EcoZap — definições centrais de billing.
Usados pelo middleware, painel e webhook Stripe.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List


@dataclass
class Plan:
    id: str
    name: str
    price_monthly: float
    msg_limit: int          # -1 = ilimitado
    agent_limit: int        # -1 = ilimitado
    features: List[str] = field(default_factory=list)
    stripe_price_id: str = ""   # preenchido via env

    @property
    def unlimited_msgs(self) -> bool:
        return self.msg_limit == -1

    @property
    def unlimited_agents(self) -> bool:
        return self.agent_limit == -1

    def allows_feature(self, feature: str) -> bool:
        return "todos" in self.features or feature in self.features

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "price_monthly": self.price_monthly,
            "msg_limit": self.msg_limit,
            "agent_limit": self.agent_limit,
            "features": self.features,
        }


# ── Catálogo de planos ────────────────────────────────────────────────────────

STARTER = Plan(
    id="starter",
    name="Starter",
    price_monthly=97.00,
    msg_limit=1000,
    agent_limit=2,
    features=["atendente", "sdr", "painel_leads"],
)

PRO = Plan(
    id="pro",
    name="Pro",
    price_monthly=197.00,
    msg_limit=5000,
    agent_limit=5,
    features=[
        "atendente", "sdr", "closer", "consultor", "trainer",
        "knowledge_bank", "painel_leads", "painel_knowledge",
    ],
)

ENTERPRISE = Plan(
    id="enterprise",
    name="Enterprise",
    price_monthly=397.00,
    msg_limit=-1,
    agent_limit=-1,
    features=["todos", "api_acesso", "suporte_prioritario", "onboarding_dedicado"],
)

PLANS: dict[str, Plan] = {
    "starter": STARTER,
    "pro": PRO,
    "enterprise": ENTERPRISE,
}

DEFAULT_PLAN = STARTER


def get_plan(plan_id: str) -> Plan:
    return PLANS.get(plan_id, DEFAULT_PLAN)
