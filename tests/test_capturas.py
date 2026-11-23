"""Capturas adjuntas: validación, envío a Claude y miniaturas en el historial."""

import base64
import io

import pytest

PIL = pytest.importorskip("PIL.Image")


def png(w=40, h=30, color=(200, 30, 30)) -> str:
    buf = io.BytesIO()
    PIL.new("RGB", (w, h), color).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def jpeg(w=20, h=20) -> str:
    buf = io.BytesIO()
    PIL.new("RGB", (w, h), (10, 120, 200)).save(buf, "JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def test_envio_con_captura(srv):
    st, j = srv.api("/api/send", {"text": "mira esto", "images": [{"media_type": "image/png", "data": png(), "thumb": jpeg()}]})
    assert st == 200, j
    ev, _ = srv.wait_for(lambda e: e["type"] == "assistant" and e["text"].startswith("Recibí"))
    assert ev["text"] == "Recibí 1 imagen(es): image/png · «mira esto»"
    assert "FAKE imagenes 1" in srv.log.read_text()

    snap = srv.poll(-1, timeout=0)
    user = next(e for e in snap["history"] if e["type"] == "user")
    assert user["images"] == [jpeg()]                             # en el historial, solo la miniatura
    assert png() not in str(snap)                                 # la imagen completa no queda guardada
    _, body = srv.api("/api/cmd", {"cmd": "audit", "q": "tarea"})
    task = body["events"][0]
    assert task["images"] == 1 and task["image_bytes"] > 0


def test_solo_imagenes_sin_texto(srv):
    imgs = [{"media_type": "image/png", "data": png()}, {"media_type": "image/jpeg", "data": jpeg()}]
    assert srv.api("/api/send", {"text": "", "images": imgs})[0] == 200
    ev, _ = srv.wait_for(lambda e: e["type"] == "assistant" and e["text"].startswith("Recibí"))
    assert ev["text"] == "Recibí 2 imagen(es): image/png, image/jpeg · «Revisa las imágenes adjuntas.»"
    user = next(e for e in srv.poll(-1, timeout=0)["history"] if e["type"] == "user")
    assert user["images"] == [None, None]                         # sin miniatura: se muestra un ícono


def test_validacion(srv):
    good = {"media_type": "image/png", "data": png()}
    cases = [
        ([{"media_type": "image/bmp", "data": png()}], "formato"),
        ([{"media_type": "image/png", "data": "no-es-base64!!"}], "no es una imagen"),
        ([{"media_type": "image/png", "data": jpeg()}], "no es una imagen"),         # el tipo no coincide
        ([{"media_type": "image/png", "data": base64.b64encode(b"<svg/>").decode()}], "no es una imagen"),
        ([good] * 6, "Máximo 5"),
        ("texto", "no válido"),
    ]
    for images, msg in cases:
        st, j = srv.api("/api/send", {"text": "x", "images": images})
        assert st == 400 and msg in j["error"], (images if isinstance(images, str) else len(images), j)
    big = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * (5 * 1024 * 1024)).decode()
    st, j = srv.api("/api/send", {"text": "x", "images": [{"media_type": "image/png", "data": big}]})
    assert st == 400 and "5 MB" in j["error"]
    st, j = srv.api("/api/send", {"text": "x" * 50_001})
    assert st == 400
    # una miniatura inválida no impide el envío: solo se descarta
    st, _ = srv.api("/api/send", {"text": "ok", "images": [{**good, "thumb": png()}]})
    assert st == 200


def test_sin_sesion_no_acepta_cuerpos_grandes(make_server):
    s = make_server()
    st, _, _ = s.request("POST", "/api/send", {"text": "x", "images": [{"media_type": "image/png", "data": "A" * 200_000}]})
    assert st in (401, 413)
