"""Segundo factor (TOTP): algoritmo, activación, inicio de sesión y recuperación."""

import base64
import hashlib
import hmac
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import subprocess
import sys

from conftest import ROOT, TOKEN, clean_env, run_python


def code_at(secret: str, offset: int = 0) -> str:
    """Implementación independiente (RFC 6238) para no probar el servidor contra sí mismo."""
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    counter = int(time.time() // 30) + offset
    d = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    o = d[-1] & 15
    return f"{(int.from_bytes(d[o:o + 4], 'big') & 0x7FFFFFFF) % 10**6:06d}"


def form(s, path: str, **fields) -> tuple[int, str]:
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(s.base + path, data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with s._opener().open(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def authed(s) -> bool:
    return s.request("GET", "/api/session")[0] == 200


def enable(s, offset: int = 0) -> str:
    _, setup = s.api("/api/cmd", {"cmd": "totp_setup"})
    assert len(setup["secret"]) == 32 and setup["uri"].startswith("otpauth://totp/")
    assert f"secret={setup['secret']}" in setup["uri"]
    st, r = s.api("/api/cmd", {"cmd": "totp_enable", "code": code_at(setup["secret"], offset)})
    assert st == 200, r
    return setup["secret"]


def test_vectores_rfc6238(tmp_path):
    # RFC 6238, apéndice B (SHA1): clave "12345678901234567890", últimos 6 dígitos
    code = """
from claude_remoto import totp as s
import json, base64
k = base64.b32encode(b"12345678901234567890").decode()
print(json.dumps([s.totp_code(k, t // 30) for t in (59, 1111111109, 1234567890, 2000000000)]
                 + [s.totp_match(k, "287 082", now=59), s.totp_match(k, "287082", after=1, now=59),
                    s.totp_match(k, "12345", now=59)]))
"""
    proc = run_python(tmp_path, code)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out[:4] == ["287082", "081804", "005924", "279037"]
    assert out[4] == 1          # acepta espacios
    assert out[5] is None       # contador ya usado: no se reutiliza
    assert out[6] is None       # formato inválido


def test_activar_exige_codigo_valido(srv):
    assert srv.api("/api/cmd", {"cmd": "totp_enable", "code": "123456"})[0] == 400   # sin setup
    _, setup = srv.api("/api/cmd", {"cmd": "totp_setup"})
    bad = "000000" if code_at(setup["secret"]) != "000000" else "111111"
    assert srv.api("/api/cmd", {"cmd": "totp_enable", "code": bad})[0] == 400
    assert srv.api("/api/cmd", {"cmd": "sessions_list"})[1]["totp"] is False


def test_login_con_segundo_factor(srv):
    otro = srv.clone()
    otro.login()
    secret = enable(srv, -1)
    assert authed(srv) and not authed(otro)              # se cierran las demás sesiones
    assert srv.api("/api/cmd", {"cmd": "sessions_list"})[1]["totp"] is True
    assert srv.api("/api/cmd", {"cmd": "totp_setup"})[0] == 409

    nuevo = srv.clone()
    st, html = form(nuevo, "/login", token=TOKEN)        # token correcto, sin código
    assert st == 401 and "Código incorrecto" in html and 'name="code"' in html
    assert not authed(nuevo)
    st, _ = form(nuevo, "/login", token="malo", code=code_at(secret))
    assert st == 401 and not authed(nuevo)
    st, _ = form(nuevo, "/login", token=TOKEN, code=code_at(secret))
    assert st == 200 and authed(nuevo)
    reuse = srv.clone()                                  # el mismo código no sirve dos veces
    assert form(reuse, "/login", token=TOKEN, code=code_at(secret))[0] == 401

    # Enlace/QR con ?token=: no da sesión, pide el código con un ticket
    qr = srv.clone()
    st, headers, raw = qr.request("GET", f"/?token={TOKEN}")
    page = raw.decode()
    assert st == 200 and "Set-Cookie" not in headers and TOKEN not in page
    ticket = re.search(r'name="ticket" value="([^"]+)"', page).group(1)
    assert form(qr, "/login", ticket=ticket, code="000000")[0] == 401
    assert form(qr, "/login", ticket="inventado", code=code_at(secret, 1))[0] == 401
    st, _ = form(qr, "/login", ticket=ticket, code=code_at(secret, 1))
    assert st == 200 and authed(qr)

    _, body = srv.api("/api/cmd", {"cmd": "audit", "q": "2FA"})
    events = [e["event"] for e in body["events"]]
    assert "totp_enabled" in events and "login_failed" in events


def test_desactivar(srv):
    secret = enable(srv, -1)
    assert srv.api("/api/cmd", {"cmd": "totp_disable", "code": code_at(secret, -1)})[0] == 400   # ya usado
    assert srv.api("/api/cmd", {"cmd": "totp_disable", "code": code_at(secret)})[0] == 200
    nuevo = srv.clone()
    nuevo.login()                                        # vuelve a bastar el token
    assert authed(nuevo)


def test_recuperacion_desde_el_pc(make_server, tmp_path):
    s = make_server("basic")
    s.login()
    enable(s)
    s.proc.terminate(); s.proc.wait(5)
    proc = subprocess.run([sys.executable, str(ROOT / "server.py"), "--desactivar-2fa"], cwd=tmp_path,
                          env=clean_env(tmp_path), capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    state = json.loads((tmp_path / "state.json").read_text())
    assert "totp" not in state and state["token"] == TOKEN
    assert "desactivado" in proc.stdout
