"""
OPS — Sistema autônomo de monitoramento, circuit breaker e auto-recovery.
Detecta erros repetidos, desabilita tasks quebradas, tenta recuperar,
e gera relatórios. Tudo isolado — nunca toca em lógica de negócio.

Princípios:
  1. Erro detectado → tracked em Redis
  2. 5 erros consecutivos → circuit breaker abre (task pausada 30min)
  3. Auto-fix tenta resolver padrões conhecidos
  4. Health check verifica componentes a cada 30min
  5. Relatório diário no Telegram com status completo
  6. Progresso salvo em Redis → tasks retomam após interrupção
"""

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict

import redis
from app.config import get_settings
from app.services.alerts import notify_owner, notify_critical, notify_warn

logger = logging.getLogger(__name__)
settings = get_settings()

# ─────────────────── CONFIG ───────────────────

PREFIX = "ops:"
MAX_CONSECUTIVE_ERRORS = 5
CIRCUIT_COOLDOWN = 1800        # 30 min
HEALTH_TTL = 7200             # 2h
STATS_TTL = 172800             # 2 dias
PROGRESS_TTL = 86400           # 24h


def _redis():
    return redis.from_url(settings.redis_url, decode_responses=True)


# ═══════════════════════════════════════════════
#  ERROR TRACKING
# ═══════════════════════════════════════════════

def track_error(task_name: str, error: Exception) -> dict:
    """Registra erro. Se passar do limite, abre circuit breaker."""
    try:
        r = _redis()
        err_key = f"{PREFIX}err_count:{task_name}"
        last_key = f"{PREFIX}err_last:{task_name}"
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stats_key = f"{PREFIX}stats:{date}"

        count = r.incr(err_key)
        r.expire(err_key, 3600)  # reset depois de 1h sem erros

        err_info = {
            "type": type(error).__name__,
            "message": str(error)[:500],
            "count": count,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        r.set(last_key, json.dumps(err_info), ex=86400)
        r.hincrby(stats_key, f"{task_name}:err", 1)
        r.expire(stats_key, STATS_TTL)

        action = {"task": task_name, "count": count, "action": "logged"}

        if count >= MAX_CONSECUTIVE_ERRORS:
            _open_circuit(task_name, err_info)
            action["action"] = "circuit_opened"

        return action
    except Exception as e:
        logger.error("[Ops] Falha ao rastrear erro: %s", e)
        return {"task": task_name, "action": "tracking_failed"}


def track_success(task_name: str):
    """Reseta contador de erros no sucesso."""
    try:
        r = _redis()
        r.delete(f"{PREFIX}err_count:{task_name}")
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        r.hincrby(f"{PREFIX}stats:{date}", f"{task_name}:ok", 1)
        r.expire(f"{PREFIX}stats:{date}", STATS_TTL)
    except Exception as e:
        logger.error("[Ops] Falha ao rastrear sucesso: %s", e)


# ═══════════════════════════════════════════════
#  CIRCUIT BREAKER
# ═══════════════════════════════════════════════

def _open_circuit(task_name: str, err_info: dict):
    """Desabilita task temporariamente. Isolado — só toca nessa task."""
    try:
        r = _redis()
        circuit_key = f"{PREFIX}circuit:{task_name}"
        r.set(circuit_key, json.dumps(err_info), ex=CIRCUIT_COOLDOWN)

        notify_critical(
            f"Circuit Breaker ABERTO\n\n"
            f"Task: `{task_name}`\n"
            f"Erros consecutivos: {err_info['count']}\n"
            f"Erro: `{err_info['type']}: {err_info['message'][:200]}`\n"
            f"Acao: task pausada por {CIRCUIT_COOLDOWN // 60} min\n"
            f"Auto-recovery em andamento..."
        )

        _attempt_auto_fix(task_name, err_info)
    except Exception as e:
        logger.error("[Ops] Falha ao abrir circuit breaker: %s", e)


def is_circuit_open(task_name: str) -> bool:
    """Checa se o circuit breaker está aberto (task desabilitada)."""
    try:
        r = _redis()
        return r.exists(f"{PREFIX}circuit:{task_name}") > 0
    except Exception:
        return False  # na dúvida, deixa rodar


def close_circuit(task_name: str):
    """Fecha o circuit breaker manualmente."""
    try:
        r = _redis()
        r.delete(f"{PREFIX}circuit:{task_name}")
        r.delete(f"{PREFIX}err_count:{task_name}")
        notify_owner(f"Circuit breaker FECHADO para `{task_name}`. Task reativada.", level="info")
    except Exception as e:
        logger.error("[Ops] Falha ao fechar circuit: %s", e)


# ═══════════════════════════════════════════════
#  AUTO-FIX (isolado, nunca toca em outra task)
# ═══════════════════════════════════════════════

def _attempt_auto_fix(task_name: str, err_info: dict):
    """Tenta corrigir padrões conhecidos. Cada fix é isolado."""
    error_type = err_info.get("type", "")
    error_msg = err_info.get("message", "")
    fix = None

    if error_type == "ImportError":
        fix = (
            f"Task `{task_name}` desabilitada (import quebrado: {error_msg[:150]}).\n"
            f"Causa provavel: classe/funcao removida ou renomeada.\n"
            f"Acao: task pausada ate correcao no codigo."
        )

    elif error_type == "TypeError" and "missing" in error_msg and "argument" in error_msg:
        fix = (
            f"Task `{task_name}` desabilitada (assinatura incompativel).\n"
            f"Causa provavel: beat_schedule chama sem args que a task exige.\n"
            f"Acao: task pausada ate correcao no codigo."
        )

    elif error_type in ("ConnectionError", "ConnectionRefusedError", "RedisError"):
        fix = (
            f"Task `{task_name}` pausada (conexao falhou).\n"
            f"Acao: circuit breaker vai tentar reabrir em {CIRCUIT_COOLDOWN // 60} min.\n"
            f"Se o servico voltar, a task retoma automaticamente."
        )

    elif error_type in ("HTTPError", "HTTPStatusError", "ConnectError", "ConnectTimeout"):
        fix = (
            f"Task `{task_name}` pausada (API externa falhou).\n"
            f"Acao: retry automatico em {CIRCUIT_COOLDOWN // 60} min."
        )

    elif error_type == "OperationalError":
        fix = (
            f"Task `{task_name}` pausada (erro de banco).\n"
            f"Acao: circuit breaker vai testar reconexao em {CIRCUIT_COOLDOWN // 60} min."
        )

    else:
        fix = (
            f"Task `{task_name}` pausada (erro nao catalogado: {error_type}).\n"
            f"Detalhes: {error_msg[:200]}\n"
            f"Acao: pausada por {CIRCUIT_COOLDOWN // 60} min. Se persistir, precisa de analise."
        )

    if fix:
        notify_warn(f"Auto-Recovery\n\n{fix}")


# ═══════════════════════════════════════════════
#  TASK PROGRESS (retomada após interrupção)
# ═══════════════════════════════════════════════

def save_progress(task_name: str, data: dict):
    """Salva progresso de uma task para retomada."""
    try:
        r = _redis()
        key = f"{PREFIX}progress:{task_name}"
        data["saved_at"] = datetime.now(timezone.utc).isoformat()
        r.set(key, json.dumps(data, default=str), ex=PROGRESS_TTL)
    except Exception as e:
        logger.error("[Ops] Falha ao salvar progresso: %s", e)


def get_progress(task_name: str) -> Optional[dict]:
    """Recupera progresso salvo. Retorna None se não há."""
    try:
        r = _redis()
        raw = r.get(f"{PREFIX}progress:{task_name}")
        return json.loads(raw) if raw else None
    except Exception:
        return None


def clear_progress(task_name: str):
    """Limpa progresso após conclusão."""
    try:
        r = _redis()
        r.delete(f"{PREFIX}progress:{task_name}")
    except Exception:
        pass


# ═══════════════════════════════════════════════
#  HEALTH CHECK
# ═══════════════════════════════════════════════

def run_health_check() -> dict:
    """Verifica todos os componentes. Retorna relatório."""
    report = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "components": {},
        "circuits": {},
        "overall": "healthy",
    }

    # 1. Redis
    try:
        r = _redis()
        r.ping()
        report["components"]["redis"] = {"status": "ok"}
    except Exception as e:
        report["components"]["redis"] = {"status": "down", "error": str(e)[:200]}
        report["overall"] = "degraded"

    # 2. Supabase
    try:
        from app.database import get_db
        db = get_db()
        db.table("owners").select("id").limit(1).execute()
        report["components"]["supabase"] = {"status": "ok"}
    except Exception as e:
        report["components"]["supabase"] = {"status": "down", "error": str(e)[:200]}
        report["overall"] = "degraded"

    # 3. Evolution API (WhatsApp)
    try:
        import httpx
        evo_url = settings.whatsapp_api_url.rstrip("/")
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{evo_url}/")
            if resp.status_code < 500:
                report["components"]["evolution_api"] = {"status": "ok"}
            else:
                report["components"]["evolution_api"] = {"status": "error", "code": resp.status_code}
                report["overall"] = "degraded"
    except Exception as e:
        report["components"]["evolution_api"] = {"status": "down", "error": str(e)[:200]}
        report["overall"] = "degraded"

    # 4. Circuit breakers abertos
    try:
        r = _redis()
        keys = r.keys(f"{PREFIX}circuit:*")
        for key in keys:
            task = key.replace(f"{PREFIX}circuit:", "")
            ttl = r.ttl(key)
            info_raw = r.get(key)
            report["circuits"][task] = {
                "status": "open",
                "ttl_seconds": ttl,
                "error": json.loads(info_raw) if info_raw else {},
            }
            report["overall"] = "degraded"
    except Exception:
        pass

    # 5. Erros ativos
    try:
        r = _redis()
        err_keys = r.keys(f"{PREFIX}err_count:*")
        errors = {}
        for key in err_keys:
            task = key.replace(f"{PREFIX}err_count:", "")
            count = int(r.get(key) or 0)
            if count > 0:
                errors[task] = count
        if errors:
            report["error_counts"] = errors
    except Exception:
        pass

    # Salvar resultado
    try:
        r = _redis()
        r.set(f"{PREFIX}last_health", json.dumps(report, default=str), ex=HEALTH_TTL)
    except Exception:
        pass

    return report


# ═══════════════════════════════════════════════
#  OPS REPORT (Telegram)
# ═══════════════════════════════════════════════

def generate_ops_report() -> str:
    """Gera relatório formatado para Telegram."""
    health = run_health_check()

    # Stats do dia
    try:
        r = _redis()
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stats = r.hgetall(f"{PREFIX}stats:{date}") or {}
    except Exception:
        stats = {}

    # Componentes
    comp_lines = []
    for comp, info in health.get("components", {}).items():
        icon = "ok" if info["status"] == "ok" else "FALHA"
        comp_lines.append(f"  {icon} — {comp}")

    # Circuits
    circuit_lines = []
    for task, info in health.get("circuits", {}).items():
        ttl = info.get("ttl_seconds", 0)
        mins = max(ttl // 60, 0)
        circuit_lines.append(f"  `{task}` (reativa em {mins}min)")

    # Contadores
    ok_count = sum(int(v) for k, v in stats.items() if k.endswith(":ok"))
    err_count = sum(int(v) for k, v in stats.items() if k.endswith(":err"))

    # Erros ativos
    err_lines = []
    for task, count in health.get("error_counts", {}).items():
        err_lines.append(f"  `{task}`: {count} erros")

    # Montar relatório
    overall = health["overall"].upper()
    report = f"*Relatorio Ops — {overall}*\n\n"
    report += f"*Componentes:*\n" + "\n".join(comp_lines) + "\n" if comp_lines else ""

    if circuit_lines:
        report += f"\n*Circuits abertos:*\n" + "\n".join(circuit_lines) + "\n"

    report += f"\n*Hoje:*\n  Tasks OK: {ok_count}\n  Tasks com erro: {err_count}\n"

    if err_lines:
        report += f"\n*Erros ativos:*\n" + "\n".join(err_lines) + "\n"

    if not circuit_lines and not err_lines and health["overall"] == "healthy":
        report += "\nTudo operando normalmente."

    return report
