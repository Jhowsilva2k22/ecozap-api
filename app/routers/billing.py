"""
Router de Billing — EcoZap · Powered by Asaas
(PIX · Boleto · Cartão de crédito · Recorrência)

Endpoints:
  GET  /billing/plans                  — lista planos
  GET  /billing/status                 — status assinatura do owner
  POST /billing/checkout               — cria cobrança/assinatura no Asaas
  POST /billing/cancel                 — cancela assinatura
  POST /billing/asaas-webhook          — recebe notificações do Asaas

Variáveis de ambiente necessárias:
  ASAAS_API_KEY           — chave do Asaas (começa com $aact_...)
  ASAAS_ENVIRONMENT       — 'sandbox' ou 'production' (padrão: production)
  ASAAS_WEBHOOK_TOKEN     — token opcional para validar webhook (mesmo token configurado no Asaas)
"""
from __future__ import annotations
import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import get_settings
from app.models.plans import PLANS, get_plan

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["Billing"])
settings = get_settings()

# ── Constantes Asaas ─────────────────────────────────────────────────────────

ASAAS_BASE = {
    "sandbox":    "https://sandbox.asaas.com/api/v3",
    "production": "https://api.asaas.com/v3",
}


def _asaas_url(path: str) -> str:
    env = os.getenv("ASAAS_ENVIRONMENT", "production")
    return f"{ASAAS_BASE.get(env, ASAAS_BASE['production'])}{path}"


def _asaas_headers() -> dict:
    key = os.getenv("ASAAS_API_KEY", "")
    if not key:
        raise HTTPException(status_code=503, detail="ASAAS_API_KEY não configurado")
    return {"access_token": key, "Content-Type": "application/json"}


# ── Helpers internos ──────────────────────────────────────────────────────────

def _db():
    from supabase import create_client
    return create_client(settings.supabase_url, settings.supabase_service_key)


def _check_token(token: str):
    if token != settings.app_secret:
        raise HTTPException(status_code=401, detail="Token inválido")


# ── Listar planos ─────────────────────────────────────────────────────────────

@router.get("/plans")
async def list_plans():
    """Retorna os planos disponíveis (público)."""
    return [p.to_dict() for p in PLANS.values()]


# ── Status da assinatura do owner ─────────────────────────────────────────────

@router.get("/status")
async def subscription_status(
    token: str = Query(...),
    owner_id: str = Query(...),
):
    _check_token(token)
    db = _db()
    owner = (
        db.table("owners")
        .select("id, business_name, plan_id, sub_status, trial_ends_at, sub_ends_at, stripe_customer_id")
        .eq("id", owner_id)
        .maybe_single()
        .execute()
    )
    if not (owner and owner.data):
        raise HTTPException(status_code=404, detail="Owner não encontrado")

    o = owner.data
    plan = PLANS.get(o.get("plan_id", "starter"))

    from datetime import datetime
    month = datetime.utcnow().strftime("%Y-%m")
    usage_row = (
        db.table("usage_logs")
        .select("messages_count")
        .eq("owner_id", owner_id)
        .eq("month", month)
        .maybe_single()
        .execute()
    )
    used = (usage_row.data or {}).get("messages_count", 0)

    return {
        "owner_id": owner_id,
        "business_name": o.get("business_name"),
        "plan": plan.to_dict() if plan else None,
        "sub_status": o.get("sub_status", "trial"),
        "trial_ends_at": o.get("trial_ends_at"),
        "sub_ends_at": o.get("sub_ends_at"),
        "usage": {
            "month": month,
            "used": used,
            "limit": plan.msg_limit if plan else 1000,
            "pct": round(used / plan.msg_limit * 100) if plan and plan.msg_limit > 0 else 0,
        },
    }


# ── Criar cobrança / assinatura ───────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    owner_id: str
    plan_id: str
    payment_method: str = "PIX"   # PIX | BOLETO | CREDIT_CARD
    # Para cartão de crédito (opcional):
    card_holder_name: Optional[str] = None
    card_number: Optional[str] = None
    card_expiry_month: Optional[str] = None
    card_expiry_year: Optional[str] = None
    card_ccv: Optional[str] = None


@router.post("/checkout")
async def create_checkout(data: CheckoutRequest, token: str = Query(...)):
    _check_token(token)

    plan = get_plan(data.plan_id)
    db = _db()
    owner = (
        db.table("owners")
        .select("business_name, phone, stripe_customer_id")
        .eq("id", data.owner_id)
        .maybe_single()
        .execute()
    )
    if not (owner and owner.data):
        raise HTTPException(status_code=404, detail="Owner não encontrado")

    o = owner.data
    asaas_customer_id = o.get("stripe_customer_id")  # reutilizamos a coluna para o ID Asaas

    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as http:

            # 1. Cria ou reutiliza cliente no Asaas
            if not asaas_customer_id:
                phone_clean = (o.get("phone") or "").lstrip("+").replace(" ", "")
                resp = await http.post(
                    _asaas_url("/customers"),
                    headers=_asaas_headers(),
                    json={
                        "name": o.get("business_name", "EcoZap Client"),
                        "mobilePhone": phone_clean,
                        "externalReference": data.owner_id,
                        "notificationDisabled": False,
                    },
                )
                resp.raise_for_status()
                asaas_customer_id = resp.json()["id"]
                db.table("owners").update({"stripe_customer_id": asaas_customer_id}).eq("id", data.owner_id).execute()

            # 2. Cria assinatura recorrente mensal no Asaas
            from datetime import date
            today = date.today().isoformat()

            subscription_payload = {
                "customer": asaas_customer_id,
                "billingType": data.payment_method.upper(),
                "value": float(plan.price_monthly),
                "nextDueDate": today,
                "cycle": "MONTHLY",
                "description": f"EcoZap — Plano {plan.name}",
                "externalReference": data.owner_id,
            }

            # Dados de cartão de crédito (se informados)
            if data.payment_method.upper() == "CREDIT_CARD" and data.card_number:
                subscription_payload["creditCard"] = {
                    "holderName": data.card_holder_name,
                    "number": data.card_number,
                    "expiryMonth": data.card_expiry_month,
                    "expiryYear": data.card_expiry_year,
                    "ccv": data.card_ccv,
                }
                subscription_payload["creditCardHolderInfo"] = {
                    "name": data.card_holder_name,
                    "phone": (o.get("phone") or "").lstrip("+"),
                }

            resp = await http.post(
                _asaas_url("/subscriptions"),
                headers=_asaas_headers(),
                json=subscription_payload,
            )
            resp.raise_for_status()
            sub_data = resp.json()
            sub_id = sub_data.get("id")

            # 3. Atualiza owner no banco
            db.table("owners").update({
                "plan_id":       data.plan_id,
                "stripe_sub_id": sub_id,          # reutilizando coluna para sub_id Asaas
                "sub_status":    "active",
            }).eq("id", data.owner_id).execute()

            # 4. Histórico
            db.table("subscriptions").insert({
                "owner_id":      data.owner_id,
                "plan_id":       data.plan_id,
                "stripe_sub_id": sub_id,
                "status":        "active",
            }).execute()

            # Retorna link de pagamento se PIX ou Boleto
            payment_url = sub_data.get("bankSlipUrl") or sub_data.get("invoiceUrl") or ""

            # Para PIX, busca o link da primeira fatura
            if data.payment_method.upper() == "PIX" and not payment_url:
                inv_resp = await http.get(
                    _asaas_url(f"/subscriptions/{sub_id}/payments"),
                    headers=_asaas_headers(),
                )
                if inv_resp.status_code == 200:
                    payments = inv_resp.json().get("data", [])
                    if payments:
                        payment_url = payments[0].get("invoiceUrl", "")

            logger.info("[Asaas] Assinatura criada: owner=%s sub=%s plano=%s", data.owner_id[:8], sub_id, data.plan_id)

            return {
                "status": "created",
                "subscription_id": sub_id,
                "payment_method": data.payment_method,
                "payment_url": payment_url,
                "plan": plan.to_dict(),
            }

    except httpx.HTTPStatusError as e:
        detail = e.response.text if e.response else str(e)
        logger.error("[Asaas] Erro HTTP ao criar assinatura: %s", detail)
        raise HTTPException(status_code=502, detail=f"Erro Asaas: {detail}")
    except Exception as e:
        logger.error("[Asaas] Erro inesperado ao criar assinatura: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Cancelar assinatura ───────────────────────────────────────────────────────

@router.post("/cancel")
async def cancel_subscription(owner_id: str = Query(...), token: str = Query(...)):
    _check_token(token)

    db = _db()
    owner = db.table("owners").select("stripe_sub_id, sub_status").eq("id", owner_id).maybe_single().execute()
    if not (owner and owner.data):
        raise HTTPException(status_code=404, detail="Owner não encontrado")

    sub_id = owner.data.get("stripe_sub_id")
    if not sub_id:
        raise HTTPException(status_code=400, detail="Nenhuma assinatura ativa")

    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.delete(
                _asaas_url(f"/subscriptions/{sub_id}"),
                headers=_asaas_headers(),
            )
            resp.raise_for_status()

        db.table("owners").update({"sub_status": "canceled", "stripe_sub_id": None}).eq("id", owner_id).execute()
        db.table("subscriptions").update({"status": "canceled"}).eq("stripe_sub_id", sub_id).execute()

        logger.info("[Asaas] Assinatura cancelada: owner=%s sub=%s", owner_id[:8], sub_id)
        return {"status": "canceled"}

    except Exception as e:
        logger.error("[Asaas] Erro ao cancelar: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Gerar link de pagamento avulso (upgrade manual) ───────────────────────────

@router.get("/payment-link")
async def get_payment_link(
    owner_id: str = Query(...),
    plan_id: str = Query(...),
    token: str = Query(...),
):
    """Gera link de pagamento PIX para o owner escolher o plano manualmente."""
    _check_token(token)
    plan = get_plan(plan_id)
    db = _db()
    owner = db.table("owners").select("business_name, phone, stripe_customer_id").eq("id", owner_id).maybe_single().execute()
    if not (owner and owner.data):
        raise HTTPException(status_code=404, detail="Owner não encontrado")

    base = f"{settings.app_url}/billing/checkout?token={settings.app_secret}"
    return {
        "pix_link": f"{base}&owner_id={owner_id}&plan_id={plan_id}&payment_method=PIX",
        "boleto_link": f"{base}&owner_id={owner_id}&plan_id={plan_id}&payment_method=BOLETO",
        "card_link": f"{base}&owner_id={owner_id}&plan_id={plan_id}&payment_method=CREDIT_CARD",
        "plan": plan.to_dict(),
    }


# ── Webhook Asaas ─────────────────────────────────────────────────────────────

@router.post("/asaas-webhook")
async def asaas_webhook(
    request: Request,
    asaas_access_token: Optional[str] = Header(None, alias="asaas-access-token"),
):
    """
    Recebe notificações do Asaas:
    - PAYMENT_RECEIVED           → pagamento confirmado (PIX/boleto)
    - PAYMENT_OVERDUE            → pagamento vencido
    - PAYMENT_DELETED / PAYMENT_REFUNDED → reembolso/cancelamento
    - SUBSCRIPTION_DELETED       → assinatura encerrada
    """
    webhook_token = os.getenv("ASAAS_WEBHOOK_TOKEN", "")
    if webhook_token and asaas_access_token != webhook_token:
        logger.warning("[Asaas Webhook] Token inválido recebido")
        return JSONResponse(status_code=401, content={"error": "token inválido"})

    try:
        body = await request.json()
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    event   = body.get("event", "")
    payment = body.get("payment", {})
    logger.info("[Asaas Webhook] Evento: %s", event)

    try:
        if event == "PAYMENT_RECEIVED":
            await _handle_payment_received(payment)
        elif event == "PAYMENT_OVERDUE":
            await _handle_payment_overdue(payment)
        elif event in ("PAYMENT_DELETED", "PAYMENT_REFUNDED"):
            await _handle_payment_canceled(payment)
        elif event == "SUBSCRIPTION_DELETED":
            sub = body.get("subscription", {})
            await _handle_sub_deleted(sub)
        else:
            logger.debug("[Asaas Webhook] Evento ignorado: %s", event)
    except Exception as e:
        logger.error("[Asaas Webhook] Erro ao processar %s: %s", event, e)

    return JSONResponse(content={"received": True})


# ── Handlers de eventos ───────────────────────────────────────────────────────

async def _handle_payment_received(payment: dict):
    sub_id   = payment.get("subscription")
    owner_id = payment.get("externalReference")
    value    = payment.get("value", 0)

    if not owner_id and not sub_id:
        return

    db = _db()

    # Localiza owner pelo externalReference ou pela coluna stripe_sub_id
    if not owner_id and sub_id:
        row = db.table("owners").select("id").eq("stripe_sub_id", sub_id).maybe_single().execute()
        owner_id = (row.data or {}).get("id") if row else None

    if not owner_id:
        return

    db.table("owners").update({"sub_status": "active"}).eq("id", owner_id).execute()
    db.table("subscriptions").insert({
        "owner_id":       owner_id,
        "plan_id":        _get_owner_plan(db, owner_id),
        "stripe_sub_id":  sub_id,
        "stripe_invoice": payment.get("id"),
        "status":         "active",
        "amount_paid":    value,
    }).execute()

    await _send_billing_notification(
        owner_id,
        f"✅ *Pagamento confirmado!*\n\nR$ {value:.2f} recebido.\nSua equipe EcoZap segue ativa. 🚀",
    )
    logger.info("[Asaas] Pagamento recebido: owner=%s val=%.2f", owner_id[:8], value)


async def _handle_payment_overdue(payment: dict):
    owner_id = payment.get("externalReference")
    sub_id   = payment.get("subscription")

    if not owner_id and sub_id:
        db = _db()
        row = db.table("owners").select("id").eq("stripe_sub_id", sub_id).maybe_single().execute()
        owner_id = (row.data or {}).get("id") if row else None

    if not owner_id:
        return

    _db().table("owners").update({"sub_status": "past_due"}).eq("id", owner_id).execute()
    due_date = payment.get("dueDate", "")
    await _send_billing_notification(
        owner_id,
        f"⚠️ *Pagamento em atraso!*\n\n"
        f"Vencimento: {due_date}\n"
        f"Seus agentes serão pausados em breve.\n"
        f"Pague pelo link: {payment.get('invoiceUrl', settings.app_url + '/panel/billing')}",
    )
    logger.warning("[Asaas] Pagamento vencido: owner=%s", (owner_id or "?")[:8])


async def _handle_payment_canceled(payment: dict):
    owner_id = payment.get("externalReference")
    if not owner_id:
        return
    logger.info("[Asaas] Pagamento cancelado/estornado: owner=%s", owner_id[:8])


async def _handle_sub_deleted(sub: dict):
    sub_id   = sub.get("id")
    owner_id = sub.get("externalReference")

    db = _db()
    if not owner_id and sub_id:
        row = db.table("owners").select("id").eq("stripe_sub_id", sub_id).maybe_single().execute()
        owner_id = (row.data or {}).get("id") if row else None

    if not owner_id:
        return

    db.table("owners").update({"sub_status": "canceled", "stripe_sub_id": None}).eq("id", owner_id).execute()
    if sub_id:
        db.table("subscriptions").update({"status": "canceled"}).eq("stripe_sub_id", sub_id).execute()

    await _send_billing_notification(
        owner_id,
        "😔 *Assinatura encerrada*\n\nSeus agentes foram pausados. "
        f"Para reativar, acesse: {settings.app_url}/panel/billing",
    )
    logger.info("[Asaas] Assinatura encerrada: owner=%s sub=%s", owner_id[:8], sub_id)


# ── Utils ─────────────────────────────────────────────────────────────────────

def _get_owner_plan(db, owner_id: str) -> str:
    try:
        row = db.table("owners").select("plan_id").eq("id", owner_id).maybe_single().execute()
        return (row.data or {}).get("plan_id", "starter")
    except Exception:
        return "starter"


async def _send_billing_notification(owner_id: str, message: str):
    try:
        db = _db()
        owner = db.table("owners").select("phone").eq("id", owner_id).maybe_single().execute()
        if not (owner and owner.data):
            return
        from app.services.whatsapp import WhatsAppService
        wa = WhatsAppService()
        await wa.send_message(owner.data["phone"], message)
    except Exception as e:
        logger.error("[Billing] Falha ao enviar notificação WhatsApp: %s", e)
