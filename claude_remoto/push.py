"""Web Push: avisos con la app cerrada, genéricos y cifrados de punta a punta.

Para llegar a un celular con la app cerrada, el aviso tiene que pasar por el servicio de push del
navegador (Apple, Google, Mozilla o Microsoft), que está en internet. Por eso:
- El contenido va cifrado (RFC 8291, aes128gcm): el servicio de push no puede leerlo.
- Y además es genérico: solo «decisión», «terminó» o «error», sin comandos, archivos, textos ni
  nombre de pestaña. El detalle se ve al abrir la app, ya en la red local.
- Solo se envía a dispositivos que lo activaron y que en ese momento no tienen la app conectada
  (si está abierta, la propia página avisa).
- Solo a los servicios de push conocidos (no a cualquier dirección): nada de peticiones a la red interna.

Las claves VAPID se generan una vez y se guardan en el estado (permisos 600).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import ssl
import struct
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from .config import CORP_CA_BUNDLE, CORP_PROXY, log
from .state import STATE, save_state

try:   # opcional: sin «cryptography» el servidor funciona igual, solo sin Web Push
    from cryptography.hazmat.primitives import hashes, hmac, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

PUSH_ENABLED = AVAILABLE and os.getenv("PUSH", "1") not in ("0", "false", "no")
DISABLED_REASON = "" if PUSH_ENABLED else (
    "Falta el paquete «cryptography»: pip install -r requirements.txt" if not AVAILABLE
    else "Desactivados en este PC (PUSH=0)")
PUSH_DONE_AFTER = float(os.getenv("PUSH_DONE_AFTER", "30") or 30)   # solo tareas de al menos N s
PUSH_TTL = 900            # segundos que el servicio guarda el aviso si el celular no está conectado
PUSH_CONTACT = os.getenv("PUSH_CONTACT", "mailto:claude-remoto@localhost")
LIVE_POLL_SECONDS = 40    # un dispositivo en long-polling sigue «conectado» este tiempo tras su última consulta
MAX_SUBS = 20
# Servicios de push de los navegadores. PUSH_HOSTS agrega otros (separados por coma).
PUSH_HOSTS = ["fcm.googleapis.com", "android.googleapis.com", ".push.apple.com",
              ".push.services.mozilla.com", ".notify.windows.com"] + \
             [h.strip() for h in os.getenv("PUSH_HOSTS", "").split(",") if h.strip()]


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    h = hmac.HMAC(prk, hashes.SHA256())
    h.update(info + b"\x01")
    return h.finalize()[:length]


def _hmac(key: bytes, data: bytes) -> bytes:
    h = hmac.HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def _public_bytes(key: Any) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


# -- cifrado (RFC 8291 sobre RFC 8188) --------------------------------------
def encrypt(payload: bytes, ua_public: bytes, auth_secret: bytes, *,
            as_private: Any = None, salt: bytes | None = None) -> bytes:
    """Cuerpo aes128gcm listo para enviar. `as_private` y `salt` solo se fijan en las pruebas."""
    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    salt = salt or secrets.token_bytes(16)
    as_public = _public_bytes(as_private.public_key())
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh_secret = as_private.exchange(ec.ECDH(), ua_key)
    prk_key = _hmac(auth_secret, ecdh_secret)
    ikm = _hkdf_expand(prk_key, b"WebPush: info\x00" + ua_public + as_public, 32)
    prk = _hmac(salt, ikm)
    cek = _hkdf_expand(prk, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf_expand(prk, b"Content-Encoding: nonce\x00", 12)
    record = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)   # 0x02: último (y único) registro
    return salt + struct.pack("!IB", 4096, len(as_public)) + as_public + record


# -- VAPID (RFC 8292) ---------------------------------------------------------
def _vapid_key() -> Any:
    pem = (STATE.get("push") or {}).get("vapid")
    if pem:
        return serialization.load_pem_private_key(pem.encode(), password=None)
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    STATE.setdefault("push", {})["vapid"] = pem
    save_state(STATE)
    return key


def public_key() -> str:
    """Clave pública que el navegador usa para suscribirse (applicationServerKey)."""
    return b64u(_public_bytes(_vapid_key().public_key()))


def vapid_header(endpoint: str, now: float | None = None) -> str:
    u = urlparse(endpoint)
    claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int((now or time.time()) + 12 * 3600), "sub": PUSH_CONTACT}
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64u(json.dumps(claims, separators=(",", ":")).encode())
    key = _vapid_key()
    r, s = decode_dss_signature(key.sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256())))
    sig = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={head}.{body}.{sig}, k={public_key()}"


# -- suscripciones (una por dispositivo) ---------------------------------------
def _subs() -> dict[str, dict[str, Any]]:
    return STATE.setdefault("push", {}).setdefault("subs", {})


def allowed_endpoint(endpoint: str) -> bool:
    u = urlparse(endpoint or "")
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host or u.username or u.port not in (None, 443):
        return False
    return any(host == h.lstrip(".") or (h.startswith(".") and host.endswith(h)) for h in PUSH_HOSTS)


def subscribe(session: dict[str, Any], sub: Any) -> str | None:
    """Guarda la suscripción del dispositivo. Devuelve un error o None."""
    if not PUSH_ENABLED:
        return DISABLED_REASON
    if not isinstance(sub, dict):
        return "Suscripción inválida"
    endpoint = str(sub.get("endpoint") or "")
    keys = sub.get("keys") or {}
    if len(endpoint) > 2000 or not allowed_endpoint(endpoint):
        return "Servicio de push no reconocido"
    try:
        p256dh, auth = unb64u(str(keys.get("p256dh") or "")), unb64u(str(keys.get("auth") or ""))
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), p256dh)
    except (ValueError, TypeError):
        return "Claves de la suscripción inválidas"
    if len(p256dh) != 65 or len(auth) != 16:
        return "Claves de la suscripción inválidas"
    subs = _subs()
    if session["id"] not in subs and len(subs) >= MAX_SUBS:
        return f"Máximo {MAX_SUBS} dispositivos con avisos"
    subs[session["id"]] = {"endpoint": endpoint, "p256dh": b64u(p256dh), "auth": b64u(auth),
                           "created": time.time(), "label": session.get("label", "")}
    save_state(STATE)
    return None


def unsubscribe(session_id: str) -> bool:
    if _subs().pop(session_id, None) is None:
        return False
    save_state(STATE)
    return True


def subscribed(session_id: str) -> bool:
    return session_id in _subs()


# -- qué dispositivos tienen la app conectada ------------------------------------
LIVE: dict[str, int] = {}          # sesión → conexiones SSE abiertas
LAST_POLL: dict[str, float] = {}   # sesión → última consulta de long-polling


def connected(session_id: str, delta: int) -> None:
    LIVE[session_id] = LIVE.get(session_id, 0) + delta
    if LIVE[session_id] <= 0:
        LIVE.pop(session_id, None)


def polled(session_id: str) -> None:
    LAST_POLL[session_id] = time.time()


def is_live(session_id: str) -> bool:
    return LIVE.get(session_id, 0) > 0 or time.time() - LAST_POLL.get(session_id, 0) < LIVE_POLL_SECONDS


# -- envío -------------------------------------------------------------------
def _opener() -> urllib.request.OpenerDirector:
    handlers: list[Any] = []
    if CORP_PROXY:
        handlers.append(urllib.request.ProxyHandler({"https": CORP_PROXY, "http": CORP_PROXY}))
    ctx = ssl.create_default_context(cafile=os.path.expanduser(CORP_CA_BUNDLE)) if CORP_CA_BUNDLE else None
    handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def send_one(sub: dict[str, Any], message: dict[str, Any], *, urgency: str = "high") -> int:
    """Envía un aviso; devuelve el código HTTP (0 si no hubo respuesta)."""
    body = encrypt(json.dumps(message, separators=(",", ":")).encode(), unb64u(sub["p256dh"]), unb64u(sub["auth"]))
    req = urllib.request.Request(sub["endpoint"], data=body, method="POST", headers={
        "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(PUSH_TTL),
        "Urgency": urgency, "Authorization": vapid_header(sub["endpoint"])})
    try:
        with _opener().open(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:  # noqa: BLE001 — sin internet o bloqueado: los avisos son best-effort
        log.debug("No se pudo enviar el aviso push: %s", e)
        return 0


def _deliver(targets: list[tuple[str, dict[str, Any]]], message: dict[str, Any]) -> int:
    sent = 0
    for sid, sub in targets:
        code = send_one(sub, message)
        if code in (404, 410):   # la suscripción ya no existe (desinstaló, borró datos, cambió de navegador)
            unsubscribe(sid)
        elif 200 <= code < 300:
            sent += 1
        else:
            log.info("Aviso push rechazado (%s) para %s", code or "sin conexión", sub.get("label") or sid)
    return sent


async def notify(kind: str, ref: str = "", *, only: str | None = None, force: bool = False) -> int:
    """kind: decision | done | error | test. Nunca lleva detalles de la tarea."""
    from .sessions import SESSIONS  # evita un import circular
    if not PUSH_ENABLED:
        return 0
    valid = {i["id"] for i in SESSIONS.data.values()}
    for sid in [s for s in _subs() if s not in valid]:   # sesión cerrada desde 🔐 Seguridad: fuera
        unsubscribe(sid)
    targets = [(sid, dict(sub)) for sid, sub in _subs().items()
               if (only is None or sid == only) and (force or not is_live(sid))]
    if not targets:
        return 0
    return await asyncio.to_thread(_deliver, targets, {"k": kind, "ref": ref, "ts": int(time.time())})


def on_event(ev: dict[str, Any]) -> None:
    """Escucha los eventos del hub y avisa lo que importa con la app cerrada."""
    t = ev.get("type")
    if t in ("perm", "question"):
        kind = "decision"
    elif t == "done" and (ev.get("duration") or 0) >= PUSH_DONE_AFTER:
        kind = "done" if ev.get("ok") else "error"
    elif t == "error":
        kind = "error"
    else:
        return
    try:
        asyncio.get_running_loop().create_task(notify(kind, str(ev.get("ref") or "")))
    except RuntimeError:
        pass
