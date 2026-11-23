"""Servidor HTTP mínimo (asyncio, sin dependencias): páginas, SSE y long-polling."""

from __future__ import annotations

import asyncio
import json
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import push
from .agent import AGENTS, global_status
from .api import ROUTES
from .attachments import MAX_UPLOAD
from .audit import AUDIT_FILE, REQUEST, audit
from .auth import (
    FAILED_LOGINS,
    ip_allowed,
    is_authed,
    record_failure,
    start_session,
    token_ok,
    too_many_failures,
)
from .backup import MAX_BACKUP
from .config import APP_DIR, CA_CERT, COOKIE, STATIC_FILES, UI_FILE, log
from .hub import HUB
from .sessions import SESSIONS, ua_label
from .totp import LOGIN_TICKETS, new_ticket, totp_enabled, totp_verify
from .voice import MAX_AUDIO, audio_suffix, transcribe
from .voice import engine as voice_engine

SSE_PING_SECONDS = 10  # latido visible para el navegador (detecta conexiones muertas)


POLL_WAIT_SECONDS = 25  # espera máxima de una petición de long-polling


MAX_BODY = 64 * 1024


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; worker-src 'self'; manifest-src 'self'; "
        "frame-ancestors 'none'; form-action 'self'"
    ),
}


LOGIN_PAGE = """<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Claude Remoto</title>
<style>body{font-family:system-ui;background:#111;color:#eee;display:grid;place-items:center;height:100vh;margin:0}
form{display:grid;gap:12px;width:min(320px,90vw)}input,button{font-size:16px;padding:12px;border-radius:10px;border:1px solid #333;background:#1c1c1e;color:#eee}
button{background:#d97757;border:0;color:#fff;font-weight:600}p{color:#f88;margin:0}</style>
<form method="post" action="/login"><h2>🔒 Claude Remoto</h2>%s
<button>Entrar</button></form></html>"""


TOKEN_FIELD = '<input name="token" type="password" placeholder="Token de acceso" autocomplete="current-password" autofocus>'


CODE_FIELD = ('<input name="code" inputmode="numeric" pattern="[0-9 ]{6,7}" maxlength="7" required '
              'placeholder="Código de 6 dígitos (app de autenticación)" autocomplete="one-time-code"%s>')


def login_page(error: str = "", ticket: str = "") -> str:
    msg = f"<p>{error}</p>" if error else ""
    if ticket:  # el token ya se validó (enlace/QR): solo falta el código
        fields = f'<input type="hidden" name="ticket" value="{ticket}">' + CODE_FIELD % " autofocus"
    else:
        fields = TOKEN_FIELD + (CODE_FIELD % "" if totp_enabled() else "")
    return LOGIN_PAGE % (msg + fields)


def http_response(status: int, body: bytes | str = b"", ctype: str = "text/plain; charset=utf-8",
                  headers: dict[str, str] | None = None) -> bytes:
    if isinstance(body, str):
        body = body.encode()
    h = {"Content-Type": ctype, "Content-Length": str(len(body)), "Connection": "close",
         **SECURITY_HEADERS, **(headers or {})}
    head = f"HTTP/1.1 {status} {HTTPStatus(status).phrase}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in h.items())
    return head.encode() + b"\r\n" + body


def json_response(status: int, data: dict) -> bytes:
    return http_response(status, json.dumps(data, ensure_ascii=False), "application/json; charset=utf-8")


async def read_head(reader: asyncio.StreamReader):
    """Línea de petición y cabeceras; el cuerpo se lee aparte, cuando ya se sabe si se acepta."""
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=15)
    lines = head.decode("latin-1").split("\r\n")
    method, target, _ = lines[0].split(" ", 2)
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    length = int(headers.get("content-length", "0") or 0)
    if length < 0:
        raise ValueError("Content-Length inválido")
    return method.upper(), target, headers, length


async def read_body(reader: asyncio.StreamReader, length: int, timeout: float = 15) -> bytes:
    return await asyncio.wait_for(reader.readexactly(length), timeout=timeout) if length else b""


async def api_transcribe(writer: asyncio.StreamWriter, headers: dict[str, str], body: bytes) -> None:
    """Audio del botón 🎤 → texto, transcrito en este PC con Whisper."""
    ctype = headers.get("content-type", "")
    # Misma defensa CSRF que la API: cabecera propia (otro sitio no puede enviarla sin permiso)
    if headers.get("x-cr") != "1" or not audio_suffix(ctype):
        writer.write(json_response(403, {"error": "Solicitud rechazada"}))
        return
    if voice_engine() != "whisper":
        writer.write(json_response(409, {"error": "Whisper no está instalado en el PC (ver README, «Voz»)"}))
        return
    if not body:
        writer.write(json_response(400, {"error": "Audio vacío"}))
        return
    t = time.time()
    try:
        text = await transcribe(body, ctype)
    except Exception:  # noqa: BLE001
        log.exception("Error al transcribir")
        writer.write(json_response(500, {"error": "No se pudo transcribir el audio"}))
        return
    secs = round(time.time() - t, 1)
    audit("voice", bytes=len(body), seconds=secs, chars=len(text))
    writer.write(json_response(200, {"text": text, "seconds": secs}))


async def sse(writer: asyncio.StreamWriter, session: dict[str, Any] | None = None) -> None:
    writer.write(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream; charset=utf-8\r\n"
        b"Cache-Control: no-store, no-transform\r\nConnection: keep-alive\r\nX-Accel-Buffering: no\r\n\r\n"
        # Relleno inicial: algunos proxies/antivirus no entregan nada hasta acumular ~2 KB
        + b":" + b" " * 2048 + b"\n"
        + b"retry: 3000\n\n"
    )
    q: asyncio.Queue = asyncio.Queue()
    HUB.clients.add(q)
    try:
        writer.write(f"data: {json.dumps(HUB.snapshot(global_status(), AGENTS.streams()), ensure_ascii=False)}\n\n".encode())
        await writer.drain()
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=SSE_PING_SECONDS)
                if ev is None:  # el hub nos desconectó
                    break
                writer.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
            except asyncio.TimeoutError:
                if session and session not in SESSIONS.data.values():
                    break  # la sesión fue cerrada desde otro dispositivo
                # Latido como evento con nombre: el navegador SÍ lo ve (un comentario no),
                # así detecta cuándo la conexión murió en silencio.
                writer.write(f"event: ping\ndata: {HUB.seq}\n\n".encode())
            await asyncio.wait_for(writer.drain(), timeout=SSE_PING_SECONDS * 2)
    except (ConnectionError, asyncio.CancelledError, asyncio.TimeoutError):
        pass
    finally:
        HUB.clients.discard(q)


async def poll(query: str) -> dict[str, Any]:
    """Long-polling: alternativa cuando un proxy o antivirus retiene el flujo SSE."""
    qs = parse_qs(query)
    try:
        after = int(qs.get("after", ["0"])[0])
    except ValueError:
        after = -1
    try:  # espera opcional más corta (0–25 s)
        wait = max(0.0, min(POLL_WAIT_SECONDS, float(qs.get("wait", [POLL_WAIT_SECONDS])[0])))
    except ValueError:
        wait = POLL_WAIT_SECONDS
    events = HUB.since(after)
    if events == [] and wait:
        await HUB.wait(wait)
        events = HUB.since(after)
    if events is None:
        return HUB.snapshot(global_status(), AGENTS.streams())
    return {"type": "batch", "seq": HUB.seq, "events": events, "status": global_status(),
            "streams": AGENTS.streams()}


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername") or ("?", 0)
    ip = str(peer[0])
    try:
        if not ip_allowed(ip):
            log.warning("Rechazada conexión desde %s (fuera de ALLOWED_NETS)", ip)
            return
        try:
            method, target, headers, length = await read_head(reader)
        except Exception:  # noqa: BLE001
            writer.write(http_response(400, "Solicitud inválida"))
            return
        url = urlparse(target)
        path = url.path
        # Cuerpos grandes solo para el audio de voz y las capturas, y solo con una sesión válida
        big = {"/api/transcribe": MAX_AUDIO, "/api/send": MAX_UPLOAD, "/api/backup": MAX_BACKUP}.get(path)
        limit = big if (big and method == "POST" and is_authed(headers, ip)) else MAX_BODY
        if length > limit:
            writer.write(http_response(413, "Contenido demasiado grande"))
            return
        try:
            body = await read_body(reader, length, timeout=60 if length > MAX_BODY else 15)
        except Exception:  # noqa: BLE001
            writer.write(http_response(400, "Solicitud inválida"))
            return

        # --- archivos públicos (service worker, manifiesto, ícono, CA) ---
        if method == "GET" and path in STATIC_FILES:
            name, ctype = STATIC_FILES[path]
            extra = {"Service-Worker-Allowed": "/"} if path == "/sw.js" else {}
            writer.write(http_response(200, (APP_DIR / name).read_bytes(), ctype, extra))
            return
        if method == "GET" and path == "/ca.crt":
            if CA_CERT and Path(CA_CERT).is_file():
                # iOS reconoce este tipo y ofrece instalar el perfil; Android lo descarga
                writer.write(http_response(200, Path(CA_CERT).read_bytes(), "application/x-x509-ca-cert",
                                           {"Content-Disposition": 'attachment; filename="claude-remoto-ca.crt"'}))
            else:
                writer.write(http_response(404, "CA no configurada (ver gen-certs.sh)"))
            return

        # --- login ---
        if path == "/login" and method == "POST":
            if too_many_failures(ip):
                audit("login_blocked", ip=ip)
                writer.write(http_response(429, "Demasiados intentos. Espera 10 minutos."))
                return
            form = parse_qs(body.decode("utf-8", "replace"))
            token, code, ticket = (form.get(k, [""])[0] for k in ("token", "code", "ticket"))
            device = ua_label(headers.get("user-agent", ""))
            html = "text/html; charset=utf-8"
            if ticket:  # segundo paso del enlace/QR
                t = LOGIN_TICKETS.get(ticket)
                if not t or t["exp"] < time.time() or t["ip"] != ip:
                    LOGIN_TICKETS.pop(ticket, None)
                    writer.write(http_response(401, login_page("El enlace expiró: vuelve a abrirlo"), html))
                    return
                if totp_verify(code):
                    del LOGIN_TICKETS[ticket]
                    FAILED_LOGINS.pop(ip, None)
                    writer.write(http_response(303, headers={"Location": "/", "Set-Cookie": start_session(ip, headers, "enlace/QR + 2FA")}))
                    return
                record_failure(ip)
                t["tries"] += 1
                if t["tries"] >= 5:
                    del LOGIN_TICKETS[ticket]
                audit("login_failed", ip=ip, device=device, via="enlace/QR", reason="código 2FA incorrecto")
                writer.write(http_response(401, login_page("Código incorrecto", ticket if t["tries"] < 5 else ""), html))
                return
            if not token_ok(token):
                record_failure(ip)
                audit("login_failed", ip=ip, device=device)
                log.warning("Token incorrecto desde %s", ip)
                writer.write(http_response(401, login_page("Token incorrecto"), html))
                return
            if totp_enabled() and not totp_verify(code):
                record_failure(ip)
                audit("login_failed", ip=ip, device=device, reason="código 2FA incorrecto")
                writer.write(http_response(401, login_page("Código incorrecto o ya usado"), html))
                return
            FAILED_LOGINS.pop(ip, None)
            via = "formulario + 2FA" if totp_enabled() else "formulario"
            writer.write(http_response(303, headers={"Location": "/", "Set-Cookie": start_session(ip, headers, via)}))
            return

        if path == "/" and method == "GET":
            # Enlace directo con ?token=… (p. ej. desde el QR): guarda cookie y limpia la URL
            token = parse_qs(url.query).get("token", [""])[0]
            if token and not too_many_failures(ip):
                if token_ok(token) and totp_enabled():
                    # El token del enlace no basta: se pide el código (y la URL con el token se limpia)
                    writer.write(http_response(200, login_page(ticket=new_ticket(ip)), "text/html; charset=utf-8",
                                               {"Referrer-Policy": "no-referrer"}))
                    return
                if token_ok(token):
                    writer.write(http_response(303, headers={"Location": "/", "Set-Cookie": start_session(ip, headers, "enlace/QR")}))
                    return
                record_failure(ip)
                audit("login_failed", ip=ip, device=ua_label(headers.get("user-agent", "")), via="enlace/QR")
            if not is_authed(headers, ip):
                writer.write(http_response(200, login_page(), "text/html; charset=utf-8"))
                return
            writer.write(http_response(200, UI_FILE.read_bytes(), "text/html; charset=utf-8"))
            return

        session = is_authed(headers, ip)
        if not session:
            writer.write(json_response(401, {"error": "No autorizado"}))
            return
        REQUEST.set({"session": session, "ip": ip})

        if path == "/events" and method == "GET":
            push.connected(session["id"], 1)   # con la app abierta, avisa la página (no Web Push)
            try:
                await sse(writer, session)
            finally:
                push.connected(session["id"], -1)
            return

        if path == "/api/audit.jsonl" and method == "GET":
            data = AUDIT_FILE.read_bytes() if AUDIT_FILE.exists() else b""
            writer.write(http_response(200, data, "application/x-ndjson; charset=utf-8",
                                       {"Content-Disposition": 'attachment; filename="claude-remoto-auditoria.jsonl"'}))
            return

        if path == "/api/session" and method == "GET":
            writer.write(json_response(200, {"id": session["id"], "label": session["label"]}))
            return

        if path == "/api/poll" and method == "GET":
            push.polled(session["id"])
            writer.write(json_response(200, await poll(url.query)))
            return

        if path == "/logout" and method == "POST":
            audit("logout")
            SESSIONS.revoke(session["id"])
            writer.write(http_response(303, headers={"Location": "/", "Set-Cookie": f"{COOKIE}=; Max-Age=0; Path=/"}))
            return

        if path == "/api/transcribe" and method == "POST":
            await api_transcribe(writer, headers, body)
            return

        if path in ROUTES and method == "POST":
            # Defensa CSRF: exige cabecera propia + JSON (un formulario de otro sitio no puede enviarlas)
            if headers.get("x-cr") != "1" or "application/json" not in headers.get("content-type", ""):
                writer.write(json_response(403, {"error": "Solicitud rechazada"}))
                return
            try:
                data = json.loads(body or b"{}")
            except json.JSONDecodeError:
                writer.write(json_response(400, {"error": "JSON inválido"}))
                return
            status, result = await ROUTES[path](data)
            writer.write(json_response(status, result))
            return

        writer.write(http_response(404, "No encontrado"))
    except Exception:  # noqa: BLE001
        log.exception("Error atendiendo %s", ip)
    finally:
        try:
            await writer.drain()
            writer.close()
        except Exception:  # noqa: BLE001
            pass

