"""API JSON que usa la interfaz: enviar, decidir y comandos."""

from __future__ import annotations

import asyncio
import base64
import os
import re
import secrets
import time
from typing import Any

from . import attachments, jira
from . import templates as tpl
from .agent import (
    AGENTS,
    MAX_TAB_NAME,
    MAX_TABS,
    emit_status,
    global_status,
    unique_name,
)
from .audit import AUDIT_FILE, REQUEST, audit, audit_tail
from .claude_sessions import list_sessions, resolve_dir
from .config import ALLOWED_ROOTS, MAX_COMMENT, PERMISSION_MODES, SESSION_DAYS, log
from .decisions import PENDING, resolve
from .hub import HUB
from .rules import RULE_KINDS, rule_label, validate_rule
from .sessions import SESSIONS
from .state import STATE, save_state
from .totp import (
    TOTP_SETUP,
    qr_svg_data,
    totp_enabled,
    totp_match,
    totp_uri,
    totp_verify,
)

MAX_PROMPT = 50_000


async def api_send(body: dict[str, Any]) -> tuple[int, dict]:
    text = str(body.get("text", "")).strip()
    images, err = attachments.validate(body.get("images"))
    if err:
        return 400, {"error": err}
    if not text and not images:
        return 400, {"error": "Mensaje vacío"}
    if len(text) > MAX_PROMPT:
        return 400, {"error": f"El mensaje admite hasta {MAX_PROMPT} caracteres"}
    if not text:
        text = "Revisa la imagen adjunta." if len(images) == 1 else "Revisa las imágenes adjuntas."
    agent = AGENTS.get(body.get("tab"))
    if agent is None:
        return 404, {"error": "Esa sesión ya no existe"}
    if not agent.submit(text, images):
        return 409, {"error": "Claude sigue trabajando en esta sesión. Usa Detener para cortar."}
    extra = {"images": len(images), "image_bytes": sum(i["bytes"] for i in images)} if images else {}
    audit("task", prompt=text[:1000], cwd=agent.cwd, tab=agent.name, **extra)
    return 200, {"ok": True}


def clean_text(value: Any) -> tuple[str, str | None]:
    """Normaliza un comentario o respuesta libre: (texto, error)."""
    if value is None:
        return "", None
    if not isinstance(value, str):
        return "", "El comentario debe ser texto"
    text = value.strip()
    if len(text) > MAX_COMMENT:
        return "", f"El comentario supera los {MAX_COMMENT} caracteres"
    return text, None


async def api_decide(body: dict[str, Any]) -> tuple[int, dict]:
    pid = str(body.get("ref", ""))
    p = PENDING.get(pid)
    if p is None:
        return 404, {"error": "Decisión inexistente o expirada"}
    if p.kind == "perm":
        choice = body.get("choice")
        if p.tool_name == "ExitPlanMode":
            labels = {"y": "✅ Plan aprobado", "e": "✅ Plan aprobado · aceptar ediciones", "n": "❌ Plan rechazado"}
        else:
            labels = {"y": "✅ Aprobado", "n": "❌ Rechazado", "a": "♾️ Aprobado siempre"}
        if choice not in labels:
            return 400, {"error": "Opción inválida"}
        comment, err = clean_text(body.get("comment"))
        if err:
            return 400, {"error": err}
        if comment and choice != "n":
            return 400, {"error": "El comentario solo se usa al rechazar"}
        label = f"{labels[choice]} · «{comment}»" if comment else labels[choice]
        resolve(pid, {"choice": choice, "comment": comment}, label)
    else:
        text, err = clean_text(body.get("text"))
        if err:
            return 400, {"error": err}
        if text:  # «Otra respuesta»: texto libre en lugar de una opción
            resolve(pid, text, f"→ «{text}»")
            return 200, {"ok": True}
        idx = body.get("answer")
        if not isinstance(idx, list) or not idx or not all(isinstance(i, int) and 0 <= i < len(p.options) for i in idx):
            return 400, {"error": "Respuesta inválida"}
        if not p.multi:
            idx = idx[:1]
        answer = ", ".join(p.options[i] for i in sorted(set(idx)))
        resolve(pid, answer, f"→ {answer}")
    return 200, {"ok": True}


async def api_cmd(body: dict[str, Any]) -> tuple[int, dict]:
    cmd, arg = str(body.get("cmd", "")), str(body.get("arg", "")).strip()
    agent = AGENTS.get(body.get("tab"))   # sin «tab»: la primera sesión
    if agent is None:
        return 404, {"error": "Esa sesión ya no existe"}
    if cmd == "stop":
        audit("stop", tab=agent.name)
        await agent.stop()
    elif cmd == "new":
        audit("new_session", tab=agent.name)
        await agent.reset()
        agent.emit("info", text="🆕 Sesión nueva.")
    elif cmd == "tab_new":
        if len(AGENTS.items) >= MAX_TABS:
            return 400, {"error": f"Máximo {MAX_TABS} sesiones en paralelo (MAX_SESSIONS)"}
        name = str(body.get("name") or "").strip()[:MAX_TAB_NAME] or unique_name("Sesión")
        cwd = None
        if body.get("cwd"):
            cwd = resolve_dir(str(body["cwd"]), agent.cwd)
            if not cwd:
                return 400, {"error": "Carpeta inexistente o fuera de ALLOWED_ROOTS"}
        new = AGENTS.create(name, cwd)
        audit("tab_new", tab=new.name, cwd=new.cwd)
        new.emit("info", text=f"🗂️ Sesión «{new.name}» · 📁 {new.cwd}")
        emit_status()
        return 200, {"ok": True, "tab": new.id, "status": global_status(new.id)}
    elif cmd == "tab_rename":
        name = str(body.get("name") or "").strip()
        if not name or len(name) > MAX_TAB_NAME:
            return 400, {"error": f"El nombre debe tener entre 1 y {MAX_TAB_NAME} caracteres"}
        audit("tab_rename", tab=agent.name, name=name)
        agent.name = name
        agent.persist()
    elif cmd == "tab_close":
        if len(AGENTS.items) == 1:
            return 400, {"error": "No se puede cerrar la única sesión"}
        name = agent.name
        await AGENTS.close(agent.id)
        HUB.drop_tab(agent.id)
        audit("tab_close", tab=name)
        emit_status()
        return 200, {"ok": True, "status": global_status()}
    elif cmd == "sessions":
        return 200, {"sessions": list_sessions(agent.cwd)}
    elif cmd == "resume":
        if not re.fullmatch(r"[A-Za-z0-9-]{8,64}", arg):
            return 400, {"error": "ID de sesión inválido"}
        await agent.reset(session_id=arg)
        audit("resume", claude_session=arg, tab=agent.name)
        agent.emit("info", text=f"🔁 Sesión {arg[:8]} retomada.")
    elif cmd == "cd":
        if not arg:
            return 200, {"cwd": agent.cwd, "roots": ALLOWED_ROOTS}
        target = resolve_dir(arg, agent.cwd)
        if not target:
            return 400, {"error": "Carpeta inexistente o fuera de ALLOWED_ROOTS"}
        await agent.reset(cwd=target)
        audit("cd", cwd=target, tab=agent.name)
        agent.emit("info", text=f"📁 Carpeta: {target} (sesión nueva)")
    elif cmd == "sessions_list":
        req = REQUEST.get() or {}
        current = (req.get("session") or {}).get("id")
        return 200, {"sessions": SESSIONS.listing(current), "days": SESSION_DAYS, "totp": totp_enabled()}
    elif cmd == "totp_setup":
        if totp_enabled():
            return 409, {"error": "El segundo factor ya está activo"}
        sid = ((REQUEST.get() or {}).get("session") or {}).get("id", "")
        secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
        TOTP_SETUP[sid] = (secret, time.time() + 600)
        uri = totp_uri(secret)
        return 200, {"secret": secret, "uri": uri, "qr": qr_svg_data(uri)}
    elif cmd == "totp_enable":
        sid = ((REQUEST.get() or {}).get("session") or {}).get("id", "")
        secret, exp = TOTP_SETUP.get(sid, ("", 0))
        if not secret or exp < time.time():
            return 400, {"error": "Configuración expirada: vuelve a empezar"}
        counter = totp_match(secret, str(body.get("code") or ""))
        if counter is None:
            audit("totp_failed", step="activar")
            return 400, {"error": "Código incorrecto. Revisa la hora del teléfono e inténtalo otra vez"}
        del TOTP_SETUP[sid]
        STATE["totp"] = {"secret": secret, "last": counter, "since": time.time()}
        closed = SESSIONS.revoke_all_except(sid)  # los demás dispositivos entran ya con código
        save_state(STATE)
        audit("totp_enabled", closed=closed)
        HUB.emit("info", text="🔐 Segundo factor activado")
        return 200, {"ok": True, "closed": closed}
    elif cmd == "totp_disable":
        if not totp_enabled():
            return 409, {"error": "El segundo factor no está activo"}
        if not totp_verify(str(body.get("code") or "")):
            audit("totp_failed", step="desactivar")
            return 400, {"error": "Código incorrecto"}
        STATE.pop("totp", None)
        save_state(STATE)
        audit("totp_disabled")
        HUB.emit("info", text="🔓 Segundo factor desactivado")
        return 200, {"ok": True}
    elif cmd == "session_revoke":
        info = SESSIONS.revoke(arg)
        if not info:
            return 404, {"error": "Sesión inexistente"}
        audit("session_revoked", target=f"{info['label']} ({info['ip']})")
        return 200, {"ok": True}
    elif cmd == "sessions_revoke_others":
        current = ((REQUEST.get() or {}).get("session") or {}).get("id")
        closed = SESSIONS.revoke_all_except(current)
        audit("sessions_revoked", count=closed)
        return 200, {"ok": True, "closed": closed}
    elif cmd == "clear_history":
        HUB.clear()
        audit("history_cleared")
        return 200, {"ok": True}
    elif cmd == "audit":
        try:
            limit = max(1, min(1000, int(body.get("limit") or 200)))
        except (TypeError, ValueError):
            limit = 200
        return 200, {"events": audit_tail(limit, str(body.get("q") or "")), "file": str(AUDIT_FILE)}
    elif cmd == "rotate_token":
        if os.getenv("ACCESS_TOKEN"):
            return 409, {"error": "El token está definido en .env (ACCESS_TOKEN): cámbialo ahí y reinicia"}
        STATE["token"] = secrets.token_urlsafe(24)
        current = ((REQUEST.get() or {}).get("session") or {}).get("id")
        closed = SESSIONS.revoke_all_except(current)
        save_state(STATE)
        log.warning("🔑 Token de acceso rotado; %d sesiones cerradas", closed)
        audit("token_rotated", closed=closed)
        return 200, {"ok": True, "token": STATE["token"], "closed": closed}
    elif cmd == "rules":
        return 200, {"rules": agent.rules, "kinds": RULE_KINDS}
    elif cmd == "rule_add":
        rule, err = validate_rule(body)
        if err:
            return 400, {"error": err}
        if any(r["action"] == rule["action"] and r["kind"] == rule["kind"] and r["pattern"] == rule["pattern"]
               for r in agent.rules):
            return 409, {"error": "Esa regla ya existe"}
        agent.rules.append(rule)
        save_state(STATE)
        audit("rule_add", rule=rule_label(rule), action=rule["action"])
        verb = "Aprobar siempre" if rule["action"] == "allow" else "Bloquear"
        HUB.emit("info", text=f"⚖️ Regla agregada: {verb} · {rule_label(rule)}")
        return 200, {"ok": True, "rule": rule, "rules": agent.rules}
    elif cmd == "rule_del":
        rule = next((r for r in agent.rules if r["id"] == arg), None)
        if rule is None:
            return 404, {"error": "Regla inexistente"}
        agent.rules.remove(rule)
        save_state(STATE)
        audit("rule_del", rule=rule_label(rule), action=rule["action"])
        HUB.emit("info", text=f"⚖️ Regla eliminada: {rule_label(rule)}")
        return 200, {"ok": True, "rules": agent.rules}
    elif cmd == "jira_set":
        conf, err = jira.save(body)
        if err:
            return 400, {"error": err}
        audit("jira_set", url=conf["url"], projects=",".join(conf["projects"]))
        emit_status()   # todos los dispositivos usan la nueva dirección
        return 200, {"ok": True, "jira": conf}
    elif cmd == "commands":
        try:
            items = await asyncio.wait_for(agent.commands(), timeout=60)
        except asyncio.TimeoutError:
            return 504, {"error": "Claude Code tardó demasiado en responder"}
        except Exception as e:  # noqa: BLE001
            log.exception("No se pudo obtener la lista de comandos")
            return 502, {"error": f"No se pudo consultar a Claude Code: {e}"}
        return 200, {"commands": items, "cwd": agent.cwd}
    elif cmd == "templates":
        return 200, {"templates": tpl.templates()}
    elif cmd in ("template_save", "template_del", "template_move", "templates_reset"):
        if cmd == "template_save":
            item, err = tpl.save(body)
            if err:
                return (404 if "inexistente" in err else 400), {"error": err}
            audit("template_save", name=item["name"], new=not body.get("id"))
        elif cmd == "template_del":
            item = tpl.delete(arg)
            if item is None:
                return 404, {"error": "Plantilla inexistente"}
            audit("template_del", name=item["name"])
        elif cmd == "template_move":
            if not tpl.move(arg, int(body.get("delta") or 0)):
                return 404, {"error": "Plantilla inexistente"}
        else:
            tpl.reset()
            audit("templates_reset")
        emit_status()   # la versión nueva hace que todos recarguen la lista
        return 200, {"ok": True, "templates": tpl.templates()}
    elif cmd == "mode":
        if arg not in PERMISSION_MODES:
            return 400, {"error": "Modo no permitido"}
        if arg != agent.mode:
            await agent.set_mode(arg)
    elif cmd == "status":
        pass
    else:
        return 400, {"error": "Comando desconocido"}
    emit_status()
    return 200, {"ok": True, "status": global_status(agent.id)}


ROUTES = {"/api/send": api_send, "/api/decide": api_decide, "/api/cmd": api_cmd}

