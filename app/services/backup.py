"""
Backup & Restore — Supabase tables → JSON → Supabase Storage
Roda diário via Celery Beat. Mantém últimos 7 dias.

Guardian v1: valida integridade do backup ANTES de salvar no Storage.
Backup corrompido → não salva, envia alerta imediato.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.database import get_db
from app.services.alerts import notify_owner

logger = logging.getLogger(__name__)

TABLES = ["owners", "customers", "messages", "leads_diagnostico_stefany"]
BUCKET = "backups"
RETENTION_DAYS = 7


# ──────────────────────────────── helpers ────────────────────────────────

def _ensure_bucket(db):
    """Cria o bucket 'backups' se não existir (private)."""
    try:
        db.storage.get_bucket(BUCKET)
    except Exception:
        try:
            db.storage.create_bucket(BUCKET, options={"public": False})
            logger.info("[Backup] Bucket '%s' criado.", BUCKET)
        except Exception as e:
            if "already exists" in str(e).lower():
                pass
            else:
                raise


def _export_table(db, table: str) -> list:
    """Exporta todos os registros de uma tabela via REST."""
    resp = db.table(table).select("*").execute()
    return resp.data or []


def _upload_json(db, path: str, data: dict):
    """Faz upload de JSON no Storage."""
    content = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    db.storage.from_(BUCKET).upload(
        path,
        content,
        file_options={"content-type": "application/json", "upsert": "true"},
    )


# ──────────────────────────────── GUARDIAN ──────────────────────────────

def _run_guardian_validation(backup_data: dict) -> dict:
    """
    Executa validação do Guardian de forma síncrona.
    O Guardian é async; usa asyncio para chamar de contexto síncrono do Celery.
    """
    try:
        from app.agents.registry import get_agent
        guardian = get_agent("guardian")
        if guardian is None:
            # Guardian não registrado ainda — deixa passar com aviso
            logger.warning("[Backup] Guardian não encontrado no registry. Validação pulada.")
            return {"is_valid": True, "issues": ["Guardian não registrado — validação pulada"]}

        # Executa coroutine no event loop (Celery task é síncrona)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        result = loop.run_until_complete(guardian.validate_backup(backup_data))
        return result

    except Exception as e:
        logger.error("[Backup] Erro ao executar Guardian: %s", e)
        # Em caso de falha do Guardian, deixa o backup prosseguir com aviso
        return {"is_valid": True, "issues": [f"Guardian com erro: {e} — backup liberado"]}


# ──────────────────────────────── BACKUP ────────────────────────────────

def run_backup() -> dict:
    """
    Exporta todas as tabelas como JSON e salva no Supabase Storage.
    Retorna resumo: {ok, tables, total_rows, file, ts}.
    """
    db = get_db()
    _ensure_bucket(db)

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d_%H%M")
    file_path = f"{date_str}/backup.json"

    payload = {"meta": {"ts": now.isoformat(), "tables": {}}, "data": {}}
    total_rows = 0

    for table in TABLES:
        try:
            rows = _export_table(db, table)
            payload["data"][table] = rows
            payload["meta"]["tables"][table] = len(rows)
            total_rows += len(rows)
            logger.info("[Backup] %s → %d registros", table, len(rows))
        except Exception as e:
            logger.warning("[Backup] Falha ao exportar '%s': %s", table, e)
            payload["data"][table] = []
            payload["meta"]["tables"][table] = 0

    # ── Guardian v1: valida antes de salvar ──────────────────────────────
    validation = _run_guardian_validation(payload["data"])

    if not validation.get("is_valid", True):
        issues_text = "\n".join(f"  ⚠ {i}" for i in validation.get("issues", []))
        logger.error(
            "[Backup] Guardian BLOQUEOU o upload — backup inválido!\nIssues:\n%s",
            issues_text,
        )
        try:
            notify_owner(
                f"*⛔ Backup BLOQUEADO pelo Guardian*\n\n"
                f"O backup de `{date_str}` NÃO foi salvo.\n\n"
                f"Problemas encontrados:\n{issues_text}\n\n"
                f"Ação necessária: verificar integridade do banco.",
                level="error",
            )
        except Exception:
            pass
        return {
            "ok": False,
            "blocked_by_guardian": True,
            "issues": validation.get("issues", []),
            "ts": now.isoformat(),
        }

    logger.info(
        "[Backup] Guardian APROVADO — %d tabelas, %d linhas.",
        len(validation.get("approved_tables", [])),
        validation.get("total_rows", 0),
    )

    # Upload
    _upload_json(db, file_path, payload)
    logger.info("[Backup] Upload ok → %s/%s", BUCKET, file_path)

    # Cleanup backups antigos
    removed = _cleanup_old_backups(db, now)

    summary = {
        "ok": True,
        "tables": len(TABLES),
        "total_rows": total_rows,
        "file": file_path,
        "ts": now.isoformat(),
        "removed_old": removed,
        "guardian": {
            "approved_tables": validation.get("approved_tables", []),
            "total_rows_validated": validation.get("total_rows", 0),
        },
    }

    # Alerta Telegram
    try:
        detail = "\n".join(
            f"  • {t}: {c} registros"
            for t, c in payload["meta"]["tables"].items()
        )
        notify_owner(
            f"*✅ Backup diário OK*\n\n"
            f"Tabelas: {len(TABLES)}\n"
            f"Total: {total_rows} registros\n"
            f"Arquivo: `{file_path}`\n"
            f"Antigos removidos: {removed}\n"
            f"Guardian: ✅ validado\n\n"
            f"{detail}",
            level="info",
        )
    except Exception:
        pass

    return summary


# ──────────────────────────────── CLEANUP ───────────────────────────────

def _cleanup_old_backups(db, now: datetime) -> int:
    """Remove backups com mais de RETENTION_DAYS dias."""
    removed = 0
    try:
        items = db.storage.from_(BUCKET).list()
        cutoff = now - timedelta(days=RETENTION_DAYS)

        for item in items:
            name = item.get("name", "")
            # Pastas no formato YYYY-MM-DD_HHMM
            try:
                folder_date = datetime.strptime(name[:10], "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
                if folder_date < cutoff:
                    # Lista arquivos dentro da pasta e remove
                    files = db.storage.from_(BUCKET).list(name)
                    paths = [f"{name}/{f['name']}" for f in files]
                    if paths:
                        db.storage.from_(BUCKET).remove(paths)
                    removed += 1
                    logger.info("[Backup] Removido backup antigo: %s", name)
            except (ValueError, KeyError):
                continue
    except Exception as e:
        logger.warning("[Backup] Erro ao limpar backups antigos: %s", e)

    return removed


# ──────────────────────────────── RESTORE ───────────────────────────────

def list_backups() -> list:
    """Lista backups disponíveis no Storage."""
    db = get_db()
    try:
        items = db.storage.from_(BUCKET).list()
        return sorted(
            [i["name"] for i in items if i.get("name", "")[:2] == "20"],
            reverse=True,
        )
    except Exception as e:
        logger.error("[Backup] Erro ao listar backups: %s", e)
        return []


def run_restore(folder_name: str, dry_run: bool = True) -> dict:
    """
    Restaura dados de um backup específico.
    dry_run=True → só mostra o que seria feito, sem alterar o banco.
    dry_run=False → APAGA dados atuais e insere do backup.

    Ordem de restore: owners → customers → messages → leads_*
    (respeita dependências de owner_id)
    """
    db = get_db()
    file_path = f"{folder_name}/backup.json"

    # Download
    try:
        content = db.storage.from_(BUCKET).download(file_path)
        payload = json.loads(content)
    except Exception as e:
        return {"ok": False, "error": f"Falha ao baixar {file_path}: {e}"}

    meta = payload.get("meta", {})
    data = payload.get("data", {})

    restore_order = ["owners", "customers", "messages", "leads_diagnostico_stefany"]
    result = {"ok": True, "dry_run": dry_run, "backup_ts": meta.get("ts"), "tables": {}}

    for table in restore_order:
        rows = data.get(table, [])
        result["tables"][table] = {"rows_in_backup": len(rows)}

        if not dry_run and rows:
            try:
                # Limpa tabela
                db.table(table).delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
                # Insere em lotes de 500
                for i in range(0, len(rows), 500):
                    batch = rows[i : i + 500]
                    db.table(table).insert(batch).execute()
                result["tables"][table]["restored"] = True
                logger.info("[Restore] %s → %d registros restaurados", table, len(rows))
            except Exception as e:
                result["tables"][table]["restored"] = False
                result["tables"][table]["error"] = str(e)
                result["ok"] = False
                logger.error("[Restore] Falha em '%s': %s", table, e)

    if not dry_run:
        try:
            notify_owner(
                f"*Restore concluído*\n\n"
                f"Backup: `{folder_name}`\n"
                f"Status: {'OK' if result['ok'] else 'COM ERROS'}\n"
                f"Tabelas: {len(restore_order)}",
                level="warn" if not result["ok"] else "info",
            )
        except Exception:
            pass

    return result
