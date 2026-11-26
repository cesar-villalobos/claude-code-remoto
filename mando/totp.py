"""Segundo factor (TOTP, RFC 6238) con la app de autenticación del teléfono."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import socket
import time
from typing import Any
from urllib.parse import quote

from .state import STATE, save_state

TOTP_STEP = 30


TOTP_ISSUER = "Mando"


TOTP_SETUP: dict[str, tuple[str, float]] = {}  # sesión → (secreto propuesto, expiración)


LOGIN_TICKETS: dict[str, dict[str, Any]] = {}  # paso intermedio: token correcto, falta el código


def totp_code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    value = int.from_bytes(digest[off:off + 4], "big") & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def totp_match(secret: str, code: str, after: int = -1, now: float | None = None) -> int | None:
    """Contador del código válido (ventana ±30 s), o None. Rechaza contadores ≤ after (reutilización)."""
    code = re.sub(r"\s", "", code or "")
    if not re.fullmatch(r"\d{6}", code):
        return None
    base = int((time.time() if now is None else now) // TOTP_STEP)
    for counter in (base - 1, base, base + 1):
        if counter > after and hmac.compare_digest(totp_code(secret, counter), code):
            return counter
    return None


def totp_enabled() -> bool:
    return bool((STATE.get("totp") or {}).get("secret"))


def totp_verify(code: str) -> bool:
    """Verifica contra el secreto activo y marca el código como usado."""
    conf = STATE.get("totp") or {}
    counter = totp_match(conf.get("secret", ""), code, int(conf.get("last", -1)))
    if counter is None:
        return False
    conf["last"] = counter
    save_state(STATE)
    return True


def totp_uri(secret: str) -> str:
    label = quote(f"{TOTP_ISSUER}:{socket.gethostname()}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(TOTP_ISSUER)}&algorithm=SHA1&digits=6&period={TOTP_STEP}"


def qr_svg_data(text: str) -> str | None:
    """QR como imagen data: (SVG) si está instalado qrcode; si no, None."""
    try:
        import qrcode  # opcional
        import qrcode.image.svg
    except ImportError:
        return None
    import io
    buf = io.BytesIO()
    qrcode.make(text, image_factory=qrcode.image.svg.SvgPathFillImage, border=2).save(buf)
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()


def new_ticket(ip: str) -> str:
    now = time.time()
    for k in [k for k, v in LOGIN_TICKETS.items() if v["exp"] < now]:
        del LOGIN_TICKETS[k]
    ticket = secrets.token_urlsafe(24)
    LOGIN_TICKETS[ticket] = {"ip": ip, "exp": now + 300, "tries": 0}
    return ticket

