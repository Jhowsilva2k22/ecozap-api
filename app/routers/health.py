"""
Health checks reais.
- /health/live  : servidor de pé, sem I/O (pra ping barato)
- /health/ready : checa Redis, Supabase, Evolution, Meta
"""
import os
import time
import httpx
from fastapi import APIRouter, Response

try:
    from redis import Redis
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False

router = APIRouter()

REDIS_URL = os.getenv("REDIS_URL", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
EVOLUTION_URL = os.getenv("EVOLUTION_API_URL", "")
EVOLUTION_KEY = os.getenv("EVOLUTION_API_KEY", "")
META_TOKEN = os.getenv("META_PAGE_TOKEN", "")
META_PAGE_ID = os.getenv("META_PAGE_ID", "")


@router.get("/health/live")
def live():
    return {"status": "ok", "ts": int(time.time())}


@router.get("/health/ready")
def ready(response: Response):
    checks = {}

    # Redis
    if HAS_REDIS and REDIS_URL:
        try:
            r = Redis.from_url(REDIS_URL, socket_connect_timeout=2)
            r.ping()
            checks["redis"] = "ok"
        except Exception as e:
            checks["redis"] = f"fail: {type(e).__name__}"
    else:
        checks["redis"] = "skip: sem config"

    # Supabase
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            h = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
            url = f"{SUPABASE_URL}/rest/v1/owners?select=id&limit=1"
            with httpx.Client(timeout=3.0) as c:
                resp = c.get(url, headers=h)
                checks["supabase"] = "ok" if resp.status_code == 200 else f"fail: {resp.status_code}"
        except Exception as e:
            checks["supabase"] = f"fail: {type(e).__name__}"
    else:
        checks["supabase"] = "skip: sem config"

    # Evolution
    if EVOLUTION_URL:
        try:
            with httpx.Client(timeout=3.0) as c:
                resp = c.get(f"{EVOLUTION_URL}/", headers={"apikey": EVOLUTION_KEY} if EVOLUTION_KEY else {})
                checks["evolution"] = "ok" if resp.status_code < 500 else f"fail: {resp.status_code}"
        except Exception as e:
            checks["evolution"] = f"fail: {type(e).__name__}"
    else:
        checks["evolution"] = "skip: sem config"

    # Meta (só se tiver token)
    if META_TOKEN and META_PAGE_ID:
        try:
            url = f"https://graph.facebook.com/v21.0/{META_PAGE_ID}?access_token={META_TOKEN}"
            with httpx.Client(timeout=3.0) as c:
                resp = c.get(url)
                checks["meta"] = "ok" if resp.status_code == 200 else f"fail: {resp.status_code}"
        except Exception as e:
            checks["meta"] = f"fail: {type(e).__name__}"
    else:
        checks["meta"] = "skip: sem credencial"

    all_ok = all(v == "ok" or v.startswith("skip") for v in checks.values())
    if not all_ok:
        response.status_code = 503
    return {
        "status": "ok" if all_ok else "degraded",
        "checks": checks,
        "ts": int(time.time()),
    }
