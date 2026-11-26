"""Enlaces a Jira: las claves como ABC-123 del chat se convierten en enlaces al ticket.

La dirección de Jira se guarda en el estado (configurable desde la interfaz) o viene de
JIRA_URL. Opcionalmente se limitan los enlaces a ciertos proyectos (JIRA_PROJECTS).
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlparse

from .state import STATE, save_state

KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
MAX_PROJECTS = 50
# Prefijos con forma de clave de Jira que casi nunca lo son (UTF-8, SHA-256, ISO-8601…)
DEFAULT_EXCLUDE = ["AES", "CVE", "GPT", "HTTP", "ISO", "MD", "PEP", "RFC", "RSA", "SHA", "TLS", "UTF", "WCAG", "X"]


def parse_projects(value: Any) -> list[str] | None:
    """«abc, DEV» → ["ABC", "DEV"]; None si alguna clave no es válida."""
    items = value if isinstance(value, list) else re.split(r"[\s,;]+", str(value or ""))
    keys = []
    for k in items:
        k = str(k).strip().upper()
        if not k:
            continue
        if not KEY_RE.match(k):
            return None
        if k not in keys:
            keys.append(k)
    return keys[:MAX_PROJECTS]


def normalize_url(value: str) -> str | None:
    """https://empresa.atlassian.net/ → https://empresa.atlassian.net; None si no es válida."""
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    u = urlparse(value)
    if u.scheme not in ("https", "http") or not u.hostname or u.query or u.fragment or u.username:
        return None
    return value


def config() -> dict[str, Any]:
    saved = STATE.get("jira") or {}
    url = saved.get("url", normalize_url(os.getenv("JIRA_URL", "")) or "")
    projects = saved.get("projects", parse_projects(os.getenv("JIRA_PROJECTS", "")) or [])
    return {"url": url, "projects": projects, "exclude": DEFAULT_EXCLUDE}


def save(body: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    url = normalize_url(str(body.get("url") or ""))
    if url is None:
        return None, "Dirección no válida: usa algo como https://tuempresa.atlassian.net"
    projects = parse_projects(body.get("projects"))
    if projects is None:
        return None, "Proyectos no válidos: claves como ABC o DEV2, separadas por comas"
    STATE["jira"] = {"url": url, "projects": projects}
    save_state(STATE)
    return config(), None
