"""API JSON que usa la interfaz: enviar, decidir y comandos."""

from __future__ import annotations

import asyncio
import base64
import os
import re
import secrets
import time
from typing import Any

from . import (
    attachments,
    backup,
    budget,
    daily,
    export_md,
    jira,
    push,
    rule_suggest,
    schedules,
    usage,
)
from . import templates as tpl
from .agent import (
    AGENTS,
    MAX_QUEUE,
    MAX_TAB_NAME,
    MAX_TABS,
    emit_status,
    global_status,
    unique_name,
)
from .audit import AUDIT_FILE, REQUEST, audit, audit_tail
from .claude_sessions import list_sessions, resolve_dir
from .config import (
    ALLOWED_ROOTS,
    MAX_COMMENT,
    PERMISSION_MODES,
    SESSION_DAYS,
    log,
)
from .decisions import PENDING, resolve
from .engines import DEFAULT_ENGINE, available_engines
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
    files, err = attachments.validate_texts(body.get("files"))
    if err:
        return 400, {"error": err}
    if not text and not images and not files:
        return 400, {"error": "Mensaje vacío"}
    if len(text) > MAX_PROMPT:
        return 400, {"error": f"El mensaje admite hasta {MAX_PROMPT} caracteres"}
    if not text and files:
        text = "Revisa el archivo adjunto." if len(files) == 1 and not images else "Revisa los archivos adjuntos."
    elif not text:
        text = "Revisa la imagen adjunta." if len(images) == 1 else "Revisa las imágenes adjuntas."
    agent = AGENTS.get(body.get("tab"))
    if agent is None:
        return 404, {"error": "Esa sesión ya no existe"}
    if images and not agent.engine.capabilities.get("images"):
        return 400, {"error": f"{agent.engine.label} no admite capturas en Mando (sí archivos de texto)"}
    result = agent.submit(text, images, files)
    if result == "full":
        return 409, {"error": f"La cola de esta sesión está llena ({MAX_QUEUE} mensajes)"}
    if result == "limit":
        audit("task_blocked", reason="spend_limit", tab=agent.name)   # sin el texto
        return 402, {"error": budget.blocked_message(), "budget": budget.info()}
    extra = {"images": len(images), "image_bytes": sum(i["bytes"] for i in images)} if images else {}
    if files:   # solo nombres y tamaños, nunca el contenido
        extra["files"] = ", ".join(f"{f['name']} ({f['bytes']} B)" for f in files)
    if result == "queued":
        extra["queued"] = True
    audit("task", prompt=text[:1000], cwd=agent.cwd, tab=agent.name, **extra)
    return 200, {"ok": True, "queued": result == "queued"}


async def run_schedule(s: dict[str, Any], missed: bool = False, *, manual: bool = False) -> str:
    """Envía la instrucción de una tarea programada a su pestaña, como si la hubieras escrito."""
    agent = AGENTS.get(s["tab"])
    if agent is None:
        schedules.set_enabled(s["id"], False)
        HUB.emit("info", text=f"⏰ «{s['name']}» se pausó: su pestaña ya no existe")
        emit_status()
        return "no_tab"
    if missed:
        agent.emit("info", text=f"⏰ «{s['name']}» no se ejecutó a su hora porque el equipo estaba apagado o dormido")
        return "missed"
    agent.emit("info", text=f"⏰ Tarea programada «{s['name']}»" + (" (ejecutada a mano)" if manual else ""))
    result = agent.submit(s["text"])
    if result == "limit":
        agent.emit("info", text="⏰ No se envió: " + budget.blocked_message())
    elif result == "full":
        agent.emit("info", text=f"⏰ No se envió: la cola de esta pestaña está llena ({MAX_QUEUE} mensajes)")
    audit("task", prompt=s["text"][:1000], cwd=agent.cwd, tab=agent.name, via="programada", schedule=s["name"],
          result=result)
    return result


def daily_summary() -> dict[str, Any]:
    return daily.build(HUB.history, {a.id: a.name for a in AGENTS})


async def send_daily_summary() -> None:
    """A la hora elegida: el resumen del día en el chat (y aviso push), solo si hubo actividad."""
    summary = daily_summary()
    if summary["tasks"] or summary["files"] or summary["approved"] or summary["rejected"]:
        HUB.emit("daily_summary", **summary)


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
        engine = str(body.get("engine") or DEFAULT_ENGINE)
        engines = {e["name"]: e for e in available_engines()}
        if engine not in engines:
            return 400, {"error": "Ese agente no está disponible en este equipo"}
        new = AGENTS.create(name, cwd, engine)
        audit("tab_new", tab=new.name, cwd=new.cwd, engine=engine)
        new.emit("info", text=f"🗂️ Sesión «{new.name}» · {engines[engine]['label']} · 📁 {new.cwd}")
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
        if not agent.engine.capabilities.get("resume"):
            return 400, {"error": f"{agent.engine.label} no permite retomar conversaciones desde aquí"}
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
    elif cmd in ("push_status", "push_subscribe", "push_unsubscribe", "push_test"):
        sid = ((REQUEST.get() or {}).get("session") or {}).get("id", "")
        session = next((i for i in SESSIONS.data.values() if i["id"] == sid), None)
        if not session:
            return 401, {"error": "Sin sesión"}
        if cmd == "push_subscribe":
            err = push.subscribe(session, body.get("sub"))
            if err:
                return 400, {"error": err}
            audit("push_subscribe", host=push.urlparse(body["sub"]["endpoint"]).hostname)
        elif cmd == "push_unsubscribe":
            if push.unsubscribe(sid):
                audit("push_unsubscribe")
        elif cmd == "push_test":
            if not push.subscribed(sid):
                return 400, {"error": "Este dispositivo no tiene los avisos activados"}
            audit("push_test")
            if not await push.notify("test", only=sid, force=True):
                return 502, {"error": "El servicio de push no aceptó el aviso. ¿Este PC tiene salida a internet (o el proxy configurado)?"}
        return 200, {"ok": True, "enabled": push.PUSH_ENABLED, "reason": push.DISABLED_REASON,
                     "subscribed": push.subscribed(sid),
                     "key": push.public_key() if push.PUSH_ENABLED else ""}
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
    elif cmd in ("rule_suggest_accept", "rule_suggest_later", "rule_suggest_never"):
        offer = rule_suggest.take(str(arg or ""))
        if offer is None:
            return 404, {"error": "Esa sugerencia ya no está disponible"}
        if cmd == "rule_suggest_accept":
            rule, err = validate_rule({"action": "allow", **offer, "note": "sugerida"})
            if err:
                return 400, {"error": err}
            if not any(r["action"] == "allow" and r["kind"] == rule["kind"] and r["pattern"] == rule["pattern"]
                       for r in agent.rules):
                agent.rules.append(rule)
                save_state(STATE)
            audit("rule_add", rule=rule_label(rule), action="allow", via="sugerencia")
            label = f"✅ Regla creada: aprobar siempre «{rule['pattern']}»"
        elif cmd == "rule_suggest_never":
            rule_suggest.dismiss_forever(offer)
            label = "🚫 No se volverá a sugerir"
        else:
            label = "⏭️ Ahora no"
        HUB.emit("rule_suggest_done", ref=arg, label=label)
        return 200, {"ok": True}
    elif cmd == "sched_list":
        return 200, {"ok": True, "items": [schedules.view(s) for s in schedules.items()], "now": time.time()}
    elif cmd == "sched_save":
        item, err = schedules.save(body, {a.id for a in AGENTS})
        if err:
            return 400, {"error": err}
        audit("sched_save", name=item["name"], when=schedules.describe(item), tab=AGENTS.get(item["tab"]).name)
        return 200, {"ok": True, "item": schedules.view(item)}
    elif cmd in ("sched_delete", "sched_toggle", "sched_run"):
        s = next((x for x in schedules.items() if x["id"] == arg), None)
        if s is None:
            return 404, {"error": "Esa tarea programada ya no existe"}
        if cmd == "sched_delete":
            schedules.delete(arg)
            audit("sched_delete", name=s["name"])
        elif cmd == "sched_toggle":
            schedules.set_enabled(arg, not s.get("enabled", True))
            audit("sched_toggle", name=s["name"], enabled=s["enabled"])
        else:
            return 200, {"ok": True, "result": await run_schedule(s, manual=True)}
        return 200, {"ok": True, "items": [schedules.view(x) for x in schedules.items()]}
    elif cmd == "daily_get":
        return 200, {"ok": True, "summary": daily_summary(), "settings": daily.settings()}
    elif cmd == "daily_set":
        conf, err = daily.save(body.get("enabled"), body.get("at"))
        if err:
            return 400, {"error": err}
        audit("daily_set", enabled=conf["enabled"], at=conf["at"])
        return 200, {"ok": True, "settings": conf}
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
    elif cmd == "queue_remove":
        if not agent.unqueue(arg, "✕ Quitado de la cola"):
            return 404, {"error": "Ese mensaje ya no está en la cola"}
        return 200, {"ok": True}
    elif cmd == "model":
        if arg not in agent.engine.models:
            return 400, {"error": "Modelo no permitido (ver CLAUDE_MODELS o CODEX_MODELS)"}
        if (arg if arg != "default" else "") != agent.model:
            await agent.set_model(arg)
    elif cmd == "export_md":
        events = [e for e in HUB.history if e.get("tab") == agent.id]
        text = export_md.to_markdown(events, name=agent.name, cwd=agent.cwd,
                                     tools=bool(body.get("tools", True)), decisions=bool(body.get("decisions", True)))
        audit("export_md", tab=agent.name)   # sin el contenido
        return 200, {"ok": True, "markdown": text,
                     "filename": f"claude-{export_md.slug(agent.name)}-{time.strftime('%Y-%m-%d')}.md"}
    elif cmd == "usage":
        return 200, {**usage.summary(), "budget": budget.info()}
    elif cmd == "budget_set":
        info, err = budget.save(body.get("limit"), body.get("warn", 80))
        if err:
            return 400, {"error": err}
        audit("budget_set", limit=info["limit"], warn=info["warn"])
        HUB.emit("info", text=f"💲 Límite de gasto mensual: US${info['limit']:.2f}" if info["limit"]
                 else "💲 Sin límite de gasto mensual")
        emit_status()
        return 200, {"ok": True, "budget": info}
    elif cmd == "budget_override":
        info = budget.allow_this_month()
        audit("budget_override", month=info["month"], spent=info["spent"], limit=info["limit"])
        HUB.emit("info", text=f"💲 Se continúa sobre el límite durante {info['month']}")
        emit_status()
        return 200, {"ok": True, "budget": info}
    elif cmd == "backup_export":
        audit("backup_export")
        return 200, {"ok": True, "backup": backup.export_data()}
    elif cmd == "mode":
        if arg not in PERMISSION_MODES or arg not in agent.engine.capabilities.get("modes", []):
            return 400, {"error": "Modo no permitido para este agente"}
        if arg != agent.mode:
            await agent.set_mode(arg)
    elif cmd == "status":
        pass
    else:
        return 400, {"error": "Comando desconocido"}
    emit_status()
    return 200, {"ok": True, "status": global_status(agent.id)}


async def api_backup(body: dict[str, Any]) -> tuple[int, dict]:
    """Importa un respaldo. Ruta aparte porque admite cuerpos más grandes que /api/cmd."""
    result, err = backup.import_data(body.get("data"), list(body.get("sections") or []),
                                     str(body.get("mode") or "merge"))
    if err:
        return 400, {"error": err}
    added = ", ".join(f"{k}: {v}" for k, v in result["added"].items()) or "nada"
    audit("backup_import", mode=body.get("mode"), added=added, skipped=len(result["skipped"]))
    HUB.emit("info", text=f"💾 Configuración importada ({added})")
    emit_status()
    return 200, {"ok": True, **result, "status": global_status(body.get("tab"))}


ROUTES = {"/api/send": api_send, "/api/decide": api_decide, "/api/cmd": api_cmd, "/api/backup": api_backup}

