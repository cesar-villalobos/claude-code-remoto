"""Auditoría: un evento por línea (JSONL) en un archivo local con permisos 600."""

from __future__ import annotations

import contextvars
import json
import os
import time
from pathlib import Path
from typing import Any

from .config import log

# Petición en curso (sesión e IP), para la auditoría y para marcar «este dispositivo»
REQUEST: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("request", default=None)


AUDIT_FILE = Path(os.getenv("AUDIT_FILE", "~/.claude-remoto-audit.jsonl")).expanduser()


AUDIT_MAX_BYTES = int(float(os.getenv("AUDIT_MAX_MB", "5")) * 1024 * 1024)


def audit(event: str, **fields: Any) -> None:
    """Registra quién (dispositivo e IP), cuándo y qué. Nunca incluye el token ni cookies."""
    req = REQUEST.get() or {}
    sess = req.get("session") or {}
    rec: dict[str, Any] = {"ts": round(time.time(), 3), "event": event}
    if sess:
        rec.update(device=sess.get("label"), session=sess.get("id"))
    if req.get("ip"):
        rec["ip"] = req["ip"]
    rec.update({k: v for k, v in fields.items() if v not in (None, "")})
    try:
        if AUDIT_FILE.exists() and AUDIT_FILE.stat().st_size > AUDIT_MAX_BYTES:
            AUDIT_FILE.replace(AUDIT_FILE.with_name(AUDIT_FILE.name + ".1"))  # se conserva un archivo anterior
        new = not AUDIT_FILE.exists()
        with AUDIT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if new:
            AUDIT_FILE.chmod(0o600)
    except OSError:
        log.exception("No se pudo escribir la auditoría")


# Palabras en español de cada evento, para que la búsqueda encuentre lo que se ve en pantalla
AUDIT_WORDS = {
    "login": "inicio de sesión", "login_failed": "token incorrecto fallido", "login_blocked": "bloqueado intentos",
    "logout": "cierre de sesión", "session_revoked": "sesión cerrada", "sessions_revoked": "sesiones cerradas",
    "token_rotated": "token rotado", "task": "tarea", "decision": "decisión", "answer": "respuesta pregunta",
    "auto_approved": "aprobado por regla", "blocked": "bloqueado por regla", "tool": "herramienta claude",
    "sched_save": "tarea programada guardada", "sched_delete": "tarea programada eliminada", "sched_toggle": "tarea programada pausada activada", "daily_set": "resumen diario configuración", "changes": "resumen de cambios de la tarea",
    "push_subscribe": "avisos web push activados", "push_unsubscribe": "avisos web push desactivados", "push_test": "aviso web push de prueba",
    "mode": "modo", "model": "modelo", "rule_add": "regla agregada", "rule_del": "regla eliminada", "stop": "detenida",
    "new_session": "sesión nueva", "resume": "sesión retomada", "cd": "carpeta",
    "history_cleared": "historial borrado", "voice": "voz dictado audio transcripción",
    "template_save": "plantilla guardada", "template_del": "plantilla eliminada",
    "templates_reset": "plantillas restablecidas", "jira_set": "jira enlaces configuración",
    "backup_export": "respaldo exportado descargado", "export_md": "conversación exportada a markdown",
    "budget_set": "límite de gasto mensual", "budget_override": "continuar sobre el límite de gasto",
    "task_blocked": "tarea bloqueada por el límite de gasto", "backup_import": "respaldo importado",
    "totp_enabled": "segundo factor 2FA activado", "totp_disabled": "segundo factor 2FA desactivado",
    "totp_failed": "código 2FA incorrecto fallido",
}


def audit_tail(limit: int = 200, query: str = "") -> list[dict[str, Any]]:
    """Últimos eventos (más recientes primero), opcionalmente filtrados por texto."""
    try:
        lines = AUDIT_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    q = query.lower().strip()
    out: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if q and q not in line.lower() and q not in AUDIT_WORDS.get(rec.get("event", ""), "").lower():
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out

