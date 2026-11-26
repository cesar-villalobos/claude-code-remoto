"""Acceso: filtro de IP, límites de intentos, token y cookies de sesión."""

from __future__ import annotations

import ipaddress
import secrets
import time
from typing import Any

from .audit import audit
from .config import (
    ALLOWED_NETS,
    COOKIE,
    GLOBAL_FAIL_LIMIT,
    SESSION_DAYS,
    TLS_CERT,
    log,
)
from .sessions import SESSIONS
from .state import STATE

FAILED_LOGINS: dict[str, list[float]] = {}


def cookie_header(raw_session: str) -> str:
    secure = "; Secure" if TLS_CERT else ""
    return f"{COOKIE}={raw_session}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_DAYS * 86400}{secure}"


def cookie_value(headers: dict[str, str]) -> str | None:
    for part in headers.get("cookie", "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE:
            return v
    return None


def token_ok(token: str) -> bool:
    return bool(token) and secrets.compare_digest(token, STATE["token"])


def start_session(ip: str, headers: dict[str, str], via: str) -> str:
    raw, info = SESSIONS.create(ip, headers.get("user-agent", ""))
    audit("login", ip=ip, device=info["label"], session=info["id"], via=via)
    return cookie_header(raw)


def is_authed(headers: dict[str, str], ip: str = "") -> dict[str, Any] | None:
    """Sesión del dispositivo, o None si no hay una válida."""
    return SESSIONS.get(cookie_value(headers), ip)


GLOBAL_FAILS: list[float] = []


GLOBAL_LOCK_SECONDS = 900


def too_many_failures(ip: str) -> bool:
    now = time.time()
    FAILED_LOGINS[ip] = [t for t in FAILED_LOGINS.get(ip, []) if now - t < 600]
    # Límite global: frena ataques distribuidos desde muchas IPs (relevante si hay acceso público)
    GLOBAL_FAILS[:] = [t for t in GLOBAL_FAILS if now - t < GLOBAL_LOCK_SECONDS]
    if len(GLOBAL_FAILS) >= GLOBAL_FAIL_LIMIT:
        return True
    return len(FAILED_LOGINS[ip]) >= 10


def record_failure(ip: str) -> None:
    now = time.time()
    FAILED_LOGINS.setdefault(ip, []).append(now)
    GLOBAL_FAILS.append(now)
    if len(GLOBAL_FAILS) == GLOBAL_FAIL_LIMIT:
        log.warning("⚠️ %d logins fallidos en %d min: login bloqueado temporalmente para todos",
                    GLOBAL_FAIL_LIMIT, GLOBAL_LOCK_SECONDS // 60)


def parse_ip(ip: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return None
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr


def in_nets(addr, nets) -> bool:
    return any(addr in net for net in nets if net.version == addr.version)


def is_local(ip: str) -> bool:
    addr = parse_ip(ip)
    return addr is not None and in_nets(addr, ALLOWED_NETS)


def ip_allowed(ip: str) -> bool:
    """Solo redes privadas (ALLOWED_NETS): las IPs públicas se rechazan siempre."""
    return is_local(ip)

