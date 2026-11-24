"""Archivos de texto adjuntos: el contenido llega a Claude, pero no queda en el historial ni en la auditoría."""

import json

from claude_remoto.attachments import validate_texts, with_files

LOG = "ERROR 12:01 conexión rechazada\nERROR 12:02 timeout SECRETO_DEL_LOG\n"


def test_validacion():
    ok, err = validate_texts([{"name": "C:\\logs\\app.log", "text": "a\r\nb"}])
    assert err is None and ok[0]["name"] == "app.log" and ok[0]["text"] == "a\nb" and ok[0]["lines"] == 2
    bad = [
        [{"name": ".env", "text": "X=1"}], [{"name": "prod.pem", "text": "k"}], [{"name": "id_ed25519", "text": "k"}],
        [{"name": "a.bin", "text": "a\x00b"}], [{"name": "big.log", "text": "x" * (200 * 1024 + 1)}],
        [{"name": f"{i}.txt", "text": "x"} for i in range(6)], [{"name": "a.txt", "text": 5}], "nada",
        [{"name": f"{i}.log", "text": "x" * 190_000} for i in range(3)],
    ]
    assert all(validate_texts(b)[1] for b in bad)
    assert validate_texts(None) == ([], None)


def test_formato_para_claude():
    files, _ = validate_texts([{"name": "x.py", "text": "print(1)\n```\nfin"}])
    msg = with_files("Revisa esto", files)
    assert msg.startswith("Revisa esto\n\nArchivo adjunto «x.py» (3 líneas):\n````py\n") and msg.endswith("\nfin\n````")


def test_llega_a_claude_y_no_se_guarda(make_server):
    s = make_server("eco")
    s.login()
    st, r = s.api("/api/send", {"text": "¿Por qué falla?", "files": [{"name": "app.log", "text": LOG}]})
    assert st == 200, r
    ev, seen = s.wait_for(lambda e: e["type"] == "assistant")
    assert "SECRETO_DEL_LOG" in ev["text"] and "Archivo adjunto «app.log» (2 líneas)" in ev["text"]
    user = [e for e in seen if e["type"] == "user"][0]
    assert user["text"] == "¿Por qué falla?" and user["files"] == [{"name": "app.log", "bytes": len(LOG.encode()), "lines": 2}]
    s.wait_for(lambda e: e["type"] == "done")
    hist = json.dumps([e for e in s.events if e["type"] in ("user", "queued")])
    assert "SECRETO_DEL_LOG" not in hist
    _, body = s.api("/api/cmd", {"cmd": "audit", "q": "app.log"})
    task = body["events"][0]
    assert task["event"] == "task" and "app.log" in task["files"] and "SECRETO" not in json.dumps(task)
    md = s.api("/api/cmd", {"cmd": "export_md"})[1]["markdown"]
    assert "`app.log`" in md and "SECRETO_DEL_LOG" not in md.split("## 🤖")[0]


def test_solo_archivo_y_en_cola(make_server):
    s = make_server("eco", FAKE_DELAY="4")
    s.login()
    s.api("/api/send", {"text": "primera"})
    st, r = s.api("/api/send", {"files": [{"name": "conf.yaml", "text": "puerto: 8080\n"}]})
    assert st == 200 and r["queued"]
    q, _ = s.wait_for(lambda e: e["type"] == "queued")
    assert q["files"][0]["name"] == "conf.yaml" and "8080" not in json.dumps(q)
    ev, _ = s.wait_for(lambda e: e["type"] == "assistant" and "conf.yaml" in e.get("text", ""), timeout=30)
    assert ev["text"].startswith("ECO:Revisa el archivo adjunto.") and "puerto: 8080" in ev["text"]
    assert s.api("/api/send", {"files": [{"name": ".env.local", "text": "TOKEN=1"}]})[0] == 400
