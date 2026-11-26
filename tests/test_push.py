"""Web Push: cifrado (vector del RFC 8291), VAPID, servicios permitidos, a quién se envía y contenido genérico."""

import json

from conftest import run_python


def py(tmp_path, code, **env):
    r = run_python(tmp_path, code, **env)
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines()


def test_cifrado_vector_rfc8291(tmp_path):
    out = py(tmp_path, """
from cryptography.hazmat.primitives.asymmetric import ec
from mando.push import encrypt, unb64u, b64u
priv = ec.derive_private_key(int.from_bytes(unb64u("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
body = encrypt(unb64u("V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24"),
               unb64u("BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"),
               unb64u("BTBZMqHH6r4Tts7J_aSIgg"), as_private=priv, salt=unb64u("DGv6ra1nlYgDCS1FRnbzlw"))
print(b64u(body))
""")
    assert out[0] == ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8w"
                      "EqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")


def test_vapid_y_servicios_permitidos(tmp_path):
    out = py(tmp_path, """
import json
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from mando import push
h = push.vapid_header("https://fcm.googleapis.com/fcm/send/abc", now=1000)
t, k = h.removeprefix("vapid t=").split(", k=")
head, body, sig = t.split(".")
pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), push.unb64u(k))
raw = push.unb64u(sig)
pub.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
           f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))
print(json.dumps(json.loads(push.unb64u(body))))
print(k == push.public_key())
ok = ["https://fcm.googleapis.com/fcm/send/x", "https://web.push.apple.com/QG", "https://updates.push.services.mozilla.com/wpush/v2/x",
      "https://wns2-par02p.notify.windows.com/w/?token=x"]
bad = ["http://fcm.googleapis.com/x", "https://10.0.0.5/x", "https://fcm.googleapis.com.evil.com/x", "https://evilpush.apple.com/x",
       "https://fcm.googleapis.com:8443/x", "https://user" + chr(64) + "fcm.googleapis.com/x", "file:///etc/passwd", ""]
print([push.allowed_endpoint(u) for u in ok], [push.allowed_endpoint(u) for u in bad])
""")
    claims = json.loads(out[0])
    assert claims == {"aud": "https://fcm.googleapis.com", "exp": 1000 + 12 * 3600, "sub": "mailto:mando@localhost"}
    assert out[1] == "True"
    assert out[2] == "[True, True, True, True] [False, False, False, False, False, False, False, False]"


def test_envio_cifrado_y_generico(tmp_path):
    """Un «servicio de push» local recibe el aviso: se descifra con la clave del navegador y no trae detalles."""
    out = py(tmp_path, """
import asyncio, json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from mando import push
from mando.sessions import SESSIONS

got = []
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        got.append((self.headers, self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(410 if "gone" in self.path else 201); self.end_headers()
    def log_message(self, *a): pass
srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"

ua = ec.generate_private_key(ec.SECP256R1()); auth = b"0123456789abcdef"
ua_pub = ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
def sub(path): return {"endpoint": base + path, "p256dh": push.b64u(ua_pub), "auth": push.b64u(auth)}

def hk(prk, info, n):
    h = hmac.HMAC(prk, hashes.SHA256()); h.update(info + b"\\x01"); return h.finalize()[:n]
def mac(k, d):
    h = hmac.HMAC(k, hashes.SHA256()); h.update(d); return h.finalize()
def decrypt(body):
    salt, idlen = body[:16], body[20]; as_pub = body[21:21 + idlen]; ct = body[21 + idlen:]
    secret = ua.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    ikm = hk(mac(auth, secret), b"WebPush: info\\x00" + ua_pub + as_pub, 32)
    prk = mac(salt, ikm)
    pt = AESGCM(hk(prk, b"Content-Encoding: aes128gcm\\x00", 16)).decrypt(hk(prk, b"Content-Encoding: nonce\\x00", 12), ct, None)
    return json.loads(pt.rstrip(b"\\x02"))

# dos dispositivos: A con la app cerrada, B con la app abierta (SSE); C con una suscripción vencida
for sid in ("A", "B", "C"):
    SESSIONS.data["h" + sid] = {"id": sid, "created": 9e9, "last_seen": 0, "ip": "", "ua": "", "label": sid}
st = push.STATE.setdefault("push", {}).setdefault("subs", {})
st["A"], st["B"], st["C"] = sub("/a"), sub("/b"), sub("/gone")
st["X"] = sub("/x")   # su sesión ya no existe: se borra
push.connected("B", 1)

async def main():
    push.on_event({"type": "perm", "ref": "r1", "title": "💻 Ejecutar comando", "detail": "rm -rf secreto", "tab": "t"})
    await asyncio.sleep(1)
    print(await push.notify("done"))
asyncio.run(main())
print(sorted(set(push.STATE["push"]["subs"])))
msgs = [decrypt(b) for h, b in got]
print(json.dumps(sorted([m["k"] for m in msgs])))
print(json.dumps(sorted(msgs, key=lambda m: m["k"])[1]))
h = got[0][0]
print(h["Content-Encoding"], h["TTL"], h["Urgency"], h["Authorization"].startswith("vapid t="))
""")
    assert out[0] == "1"                                  # «done»: solo A (B conectado, C vencido)
    assert out[1] == "['A', 'B']"                         # C (410) y X (sesión cerrada) se borran
    kinds = json.loads(out[2])
    assert kinds == ["decision", "decision", "done"]      # decisión: A y C (410, se borra); terminó: solo A
    decision = json.loads(out[3])
    assert decision["k"] == "decision" and decision["ref"] == "r1" and set(decision) == {"k", "ref", "ts"}
    assert "secreto" not in out[3] and "comando" not in out[3]       # nunca detalles
    assert out[4] == "aes128gcm 900 high True"


def test_suscripcion_por_api(srv, tmp_path):
    st, r = srv.api("/api/cmd", {"cmd": "push_status"})
    assert st == 200 and r["enabled"] and not r["subscribed"] and len(r["key"]) > 80
    bad = {"endpoint": "https://192.168.1.10/x", "keys": {"p256dh": "AA", "auth": "AA"}}
    assert srv.api("/api/cmd", {"cmd": "push_subscribe", "sub": bad})[0] == 400
    out = run_python(tmp_path, """
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from mando.push import b64u
k = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
print(b64u(k))
""").stdout.strip()
    good = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": out, "auth": "MDEyMzQ1Njc4OWFiY2RlZg"}}
    st, r = srv.api("/api/cmd", {"cmd": "push_subscribe", "sub": good})
    assert st == 200 and r["subscribed"]
    _, body = srv.api("/api/cmd", {"cmd": "audit", "q": "push"})
    assert body["events"][0]["event"] == "push_subscribe" and body["events"][0]["host"] == "fcm.googleapis.com"
    assert srv.api("/api/cmd", {"cmd": "push_unsubscribe"})[1]["subscribed"] is False
    assert srv.api("/api/cmd", {"cmd": "push_test"})[0] == 400       # sin suscripción


def test_desactivado(make_server):
    s = make_server(PUSH="0")
    s.login()
    st, r = s.api("/api/cmd", {"cmd": "push_status"})
    assert r["enabled"] is False and r["key"] == ""


def test_sin_cryptography_push_queda_desactivado(tmp_path):
    out = py(tmp_path, """
import sys
sys.modules["cryptography"] = None   # simula que no está instalado (solo para este módulo)
from mando import push
print(push.AVAILABLE, push.PUSH_ENABLED, "cryptography" in push.DISABLED_REASON)
import asyncio
print(asyncio.run(push.notify("done")))
""")
    assert out == ["False False True", "0"]
