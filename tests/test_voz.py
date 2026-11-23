"""Instrucciones por voz: motor, transcripción local y límites del audio."""

import urllib.error
import urllib.request


def post_audio(s, data: bytes, ctype="audio/webm;codecs=opus", xcr=True, cookies=True):
    h = {"Content-Type": ctype, **({"X-CR": "1"} if xcr else {})}
    req = urllib.request.Request(s.base + "/api/transcribe", data=data, method="POST", headers=h)
    try:
        with s._opener(cookies).open(req, timeout=20) as r:
            import json
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            import json
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"raw": body.decode(errors="replace")}
    except (urllib.error.URLError, ConnectionError):
        # El servidor rechaza un cuerpo excesivo sin leerlo: según el momento, el cliente ve el 413
        # o la conexión cerrada mientras aún subía datos
        return "cerrada", {}


def voice_of(s):
    return s.api("/api/cmd", {"cmd": "status"})[1]["status"]["voice"]


def test_motor_segun_configuracion(make_server):
    s = make_server(FAKE_WHISPER="0")       # sin faster-whisper instalado
    s.login()
    assert voice_of(s) == "browser"
    assert post_audio(s, b"x" * 2000)[0] == 409          # no hay Whisper: no transcribe

    off = make_server(VOICE_ENGINE="off", FAKE_WHISPER="0")
    off.login()
    assert voice_of(off) == "off"

    solo = make_server(VOICE_ENGINE="whisper", FAKE_WHISPER="0")          # pedido explícito y no instalado: no cae al navegador
    solo.login()
    assert voice_of(solo) == "off"


def test_transcripcion_con_whisper(make_server):
    s = make_server(FAKE_WHISPER="1")
    s.login()
    assert voice_of(s) == "whisper"
    st, j = post_audio(s, b"a" * 3000)
    assert st == 200 and j["text"] == "texto dictado .webm 3000 bytes"
    st, j = post_audio(s, b"b" * 200_000, ctype="audio/mp4")   # más de 64 KB (Safari, ~1 min)
    assert st == 200 and j["text"] == "texto dictado .m4a 200000 bytes"
    _, body = s.api("/api/cmd", {"cmd": "audit", "q": "voz"})
    assert body["events"][0]["event"] == "voice" and "text" not in body["events"][0]   # no guarda lo dictado


def test_transcripcion_protegida(make_server):
    s = make_server(FAKE_WHISPER="1", VOICE_MAX_MB="0.5")
    assert post_audio(s, b"a" * 3000)[0] == 401                     # sin sesión
    assert post_audio(s, b"a" * 300_000)[0] in (413, "cerrada")     # sin sesión no se aceptan cuerpos grandes
    s.login()
    assert post_audio(s, b"a" * 3000, xcr=False)[0] == 403          # CSRF: falta la cabecera propia
    assert post_audio(s, b"a" * 3000, ctype="text/plain")[0] == 403
    assert post_audio(s, b"", ctype="audio/webm")[0] == 400
    assert post_audio(s, b"a" * 600_000)[0] in (413, "cerrada")     # sobre VOICE_MAX_MB
    st, _, _ = s.request("POST", "/api/cmd", {"cmd": "status", "x": "x" * 70_000})   # la API normal sigue en 64 KB
    assert st == 413
