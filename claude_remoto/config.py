"""Configuración leída de variables de entorno (y de .env, si existe)."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    if not os.getenv("CR_NO_DOTENV"):  # las pruebas lo desactivan para no leer tu .env
        load_dotenv()
except ImportError:
    pass


BIND_HOST = os.getenv("BIND_HOST", "0.0.0.0")


PORT = int(os.getenv("PORT", "8765"))


ALLOWED_NETS = [
    ipaddress.ip_network(n.strip())
    for n in os.getenv(
        "ALLOWED_NETS", "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1/128,fe80::/10"
    ).split(",")
    if n.strip()
]


# Fuera de casa se entra por VPN (ver docs/vpn-tailscale.md): el puente reenvía con su IP local,
# así que nunca hace falta aceptar IPs públicas.


# Límite global de logins fallidos (todas las IPs juntas) antes de bloquear el login un rato
GLOBAL_FAIL_LIMIT = int(os.getenv("GLOBAL_FAIL_LIMIT", "30"))


TLS_CERT = os.getenv("TLS_CERT", "").strip()


TLS_KEY = os.getenv("TLS_KEY", "").strip()


# CA local (de gen-certs.sh) que se ofrece en /ca.crt para instalarla en el teléfono
CA_CERT = os.path.expanduser(os.getenv("CA_CERT", "").strip())


APP_DIR = Path(__file__).resolve().parent.parent  # carpeta del proyecto (ui.html, sw.js…)


# Archivos públicos (sin datos sensibles) que el navegador pide sin cookie
STATIC_FILES = {
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
}


STATE_FILE = Path(os.getenv("STATE_FILE", "~/.claude-remoto.json")).expanduser()


UI_FILE = APP_DIR / "ui.html"


DEFAULT_WORKDIR = str(Path(os.getenv("CLAUDE_WORKDIR", ".")).expanduser().resolve())


ALLOWED_ROOTS = [
    str(Path(p).expanduser().resolve())
    for p in os.getenv("ALLOWED_ROOTS", DEFAULT_WORKDIR).split(",")
    if p.strip()
]


def _mcp_servers() -> dict:
    """Servidores MCP adicionales (JSON en MCP_SERVERS), además de los configurados en Claude Code.

    Ej.: MCP_SERVERS={"docs": {"type": "http", "url": "https://mcp.ejemplo.com/mcp"}}
    ATLASSIAN_MCP_URL se sigue aceptando por compatibilidad con configuraciones anteriores.
    """
    servers: dict = {}
    raw = os.getenv("MCP_SERVERS", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
                raise ValueError("debe ser un objeto {nombre: {type, url…}}")
            servers.update(data)
        except ValueError as e:
            raise SystemExit(f"MCP_SERVERS no es un JSON válido: {e}") from None
    legacy = os.getenv("ATLASSIAN_MCP_URL", "").strip()
    if legacy and "atlassian" not in servers:
        servers["atlassian"] = {"type": "http", "url": legacy}
    return servers


MCP_SERVERS = _mcp_servers()


DECISION_TIMEOUT = int(os.getenv("DECISION_TIMEOUT_SECONDS", "900"))


MODEL = os.getenv("CLAUDE_MODEL") or None
# Modelos que se pueden elegir por pestaña. «default» = el que use Claude Code (settings o ANTHROPIC_MODEL).
# Con un gateway, pon los nombres que acepte, p. ej.: CLAUDE_MODELS=default,claude-sonnet-5,claude-opus-5
MODEL_LABELS = {"default": "Predeterminado", "sonnet": "Sonnet", "opus": "Opus", "haiku": "Haiku",
                "opusplan": "Opus para planificar"}
MODELS = [m.strip() for m in os.getenv("CLAUDE_MODELS", "default,sonnet,opus,haiku").split(",") if m.strip()] \
    or ["default"]


CORP_PROXY = os.getenv("CORP_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")


CORP_CA_BUNDLE = os.getenv("CORP_CA_BUNDLE") or os.getenv("NODE_EXTRA_CA_CERTS")


NO_PROXY = os.getenv("NO_PROXY") or os.getenv("no_proxy") or "localhost,127.0.0.1"


AUTO_APPROVE = [
    t.strip() for t in os.getenv("AUTO_APPROVE_TOOLS", "Read,Glob,Grep,LS").split(",") if t.strip()
]


AUTO_APPROVE_PREFIXES = [
    p.strip() for p in os.getenv("AUTO_APPROVE_PREFIXES", "").split(",") if p.strip()
]


GATEWAY_VARS = (
    "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_API_KEY_HELPER_TTL_MS",
)


SYSTEM_APPEND = (
    "Te están usando desde una interfaz web en el celular. Responde en español, conciso y en "
    "Markdown simple. Si tienes herramientas MCP disponibles, úsalas cuando ayuden a la tarea. "
    "Cuando debas tomar una decisión que dependa del usuario, usa AskUserQuestion con opciones claras."
)


MAX_COMMENT = 2000


# Modos de permiso que se pueden elegir desde la interfaz. bypassPermissions queda fuera a
# propósito: aprobar todo sin preguntar desde un celular es demasiado riesgoso.
PERMISSION_MODES = {
    "default": "🛡️ Preguntar",
    "acceptEdits": "✏️ Aceptar ediciones",
    "plan": "📋 Solo planificar",
}


DEFAULT_MODE = os.getenv("PERMISSION_MODE", "default")


if DEFAULT_MODE not in PERMISSION_MODES:
    DEFAULT_MODE = "default"


# Respuestas en tiempo real: texto parcial de Claude mientras escribe
STREAM_TEXT = os.getenv("STREAM_TEXT", "1") not in ("0", "false", "no")

# Resumen de los archivos que cambió cada tarea, con su diff (0 = desactivado)
CHANGES_SUMMARY = os.getenv("CHANGES_SUMMARY", "1") not in ("0", "false", "no")


STREAM_INTERVAL = 0.1  # segundos entre actualizaciones del texto parcial  # largo máximo de un comentario de rechazo o de una respuesta libre


COOKIE = "cr_session"


SESSION_DAYS = int(os.getenv("SESSION_DAYS", "30"))


logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)


log = logging.getLogger("claude-remoto")

