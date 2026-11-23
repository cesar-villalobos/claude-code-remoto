"""Sesiones en paralelo (pestañas): cada una con su Claude Code, carpeta, modo, tarea e historial."""

import json

from conftest import run_python


def status(s, tab=None):
    return s.api("/api/cmd", {"cmd": "status", **({"tab": tab} if tab else {})})[1]["status"]


def new_tab(s, **kw):
    st, r = s.api("/api/cmd", {"cmd": "tab_new", **kw})
    assert st == 200, r
    return r["tab"]


def test_una_sesion_por_defecto(srv):
    st = status(srv)
    assert [t["name"] for t in st["tabs"]] == ["Principal"] and st["tab"] == st["tabs"][0]["id"]


def test_dos_tareas_en_paralelo(srv, tmp_path):
    a = status(srv)["tab"]
    (tmp_path / "otro").mkdir()
    b = new_tab(srv, name="Front", cwd=str(tmp_path / "otro"))
    srv.api("/api/send", {"text": "tarea A", "tab": a})
    srv.api("/api/send", {"text": "tarea B", "tab": b})
    pa, _ = srv.wait_for(lambda e: e["type"] == "perm" and e.get("tab") == a)
    pb, _ = srv.wait_for(lambda e: e["type"] == "perm" and e.get("tab") == b)
    st = status(srv)
    by = {t["id"]: t for t in st["tabs"]}
    assert by[a]["busy"] and by[b]["busy"] and by[a]["pending"] == 1 and by[b]["pending"] == 1
    assert by[b]["cwd"] == str(tmp_path / "otro") and by[b]["name"] == "Front"
    assert srv.log.read_text().count("FAKE connect") == 2          # un Claude Code por sesión

    # decidir en B no afecta a A
    srv.api("/api/decide", {"ref": pb["ref"], "choice": "y"})
    ev, _ = srv.wait_for(lambda e: e["type"] == "assistant" and e.get("tab") == b and "Permiso" in e["text"])
    assert "PermissionResultAllow" in ev["text"]
    assert status(srv, a)["pending"] == 1

    # detener A cancela solo sus decisiones
    srv.api("/api/cmd", {"cmd": "stop", "tab": a})
    srv.wait_for(lambda e: e["type"] == "decided" and e["ref"] == pa["ref"])
    assert status(srv, b)["busy"] or status(srv, b)["pending"] == 1   # B sigue (ahora pregunta)


def test_eventos_y_estado_por_sesion(srv):
    b = new_tab(srv, name="B")
    srv.api("/api/send", {"text": "hola B", "tab": b})
    ev, _ = srv.wait_for(lambda e: e["type"] == "user")
    assert ev["tab"] == b
    st = status(srv, b)
    assert st["tab"] == b and st["name"] == "B" and st["busy"]       # arriba, el de la sesión pedida
    assert srv.api("/api/send", {"text": "x", "tab": "no-existe"})[0] == 404
    assert srv.api("/api/cmd", {"cmd": "mode", "arg": "plan", "tab": b})[0] == 200
    modes = {t["id"]: t["mode"] for t in status(srv)["tabs"]}
    assert modes[b] == "plan" and list(modes.values()).count("plan") == 1   # el modo es de cada sesión


def test_renombrar_cerrar_y_limite(make_server):
    s = make_server(MAX_SESSIONS="3")
    s.login()
    main = status(s)["tab"]
    b = new_tab(s)
    c = new_tab(s)
    assert [t["name"] for t in status(s)["tabs"]] == ["Principal", "Sesión", "Sesión 2"]
    st, r = s.api("/api/cmd", {"cmd": "tab_new"})
    assert st == 400 and "Máximo 3" in r["error"]
    assert s.api("/api/cmd", {"cmd": "tab_rename", "tab": b, "name": ""})[0] == 400
    assert s.api("/api/cmd", {"cmd": "tab_rename", "tab": b, "name": "x" * 31})[0] == 400
    assert s.api("/api/cmd", {"cmd": "tab_rename", "tab": b, "name": "Backend"})[0] == 200

    s.api("/api/send", {"text": "solo en C", "tab": c})
    s.wait_for(lambda e: e["type"] == "perm" and e.get("tab") == c)
    assert s.api("/api/cmd", {"cmd": "tab_close", "tab": c})[0] == 200
    snap = s.poll(-1, timeout=0)
    assert all(e.get("tab") != c for e in snap["history"])           # su historial se borra
    assert [t["name"] for t in snap["status"]["tabs"]] == ["Principal", "Backend"]
    assert s.api("/api/cmd", {"cmd": "tab_close", "tab": b})[0] == 200
    st, r = s.api("/api/cmd", {"cmd": "tab_close", "tab": main})
    assert st == 400 and "única" in r["error"]
    _, body = s.api("/api/cmd", {"cmd": "audit", "q": "sesión"})
    assert {"tab_new", "tab_close", "tab_rename"} <= {e["event"] for e in body["events"]}


def test_sesiones_sobreviven_al_reinicio(make_server, tmp_path):
    s = make_server()
    s.login()
    b = new_tab(s, name="Docs")
    s.api("/api/cmd", {"cmd": "mode", "arg": "acceptEdits", "tab": b})
    s.proc.terminate(); s.proc.wait(5)
    s2 = make_server()
    s2.login()
    tabs = status(s2)["tabs"]
    assert [t["name"] for t in tabs] == ["Principal", "Docs"] and tabs[1]["id"] == b
    assert tabs[1]["mode"] == "acceptEdits"


def test_migra_el_formato_anterior(make_server, tmp_path):
    (tmp_path / "proy").mkdir()
    state = {"token": "x", "cwd": str(tmp_path / "proy"), "session_id": "abcdef12-0000", "mode": "plan"}
    (tmp_path / "state.json").write_text(json.dumps(state))
    hist = {"seq": 2, "history": [{"id": 1, "type": "user", "text": "antes"}, {"id": 2, "type": "assistant", "text": "ok"}]}
    (tmp_path / ".claude-remoto-history.json").write_text(json.dumps(hist))
    s = make_server()
    s.login()
    st = status(s)
    assert len(st["tabs"]) == 1 and st["name"] == "Principal"
    assert st["cwd"] == str(tmp_path / "proy") and st["mode"] == "plan" and st["session"] == "abcdef12-0000"
    hist = s.poll(-1, timeout=0)["history"]
    assert [e["text"] for e in hist if e["type"] in ("user", "assistant")] == ["antes", "ok"]
    assert all(e["tab"] == st["tab"] for e in hist)                  # el historial pasa a la primera sesión


def test_historial_limitado_por_sesion(tmp_path):
    code = """
from claude_remoto import hub as h
import json
h.HISTORY_MAX = 3
hub = h.Hub()
for i in range(10): hub.emit('info', text=f'a{i}', tab='A')
hub.emit('info', text='b0', tab='B')
for i in range(10): hub.emit('info', text=f'a{10+i}', tab='A')
print(json.dumps([e['text'] for e in hub.history]))
"""
    p = run_python(tmp_path, code)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert out == ["b0", "a17", "a18", "a19"]                        # B no se pierde aunque A sea muy activa


def test_estado_al_terminar(srv):
    tab = status(srv)["tab"]
    srv.api("/api/send", {"text": "x"})
    perm, _ = srv.wait_for(lambda e: e["type"] == "perm")
    srv.api("/api/decide", {"ref": perm["ref"], "choice": "y"})
    q, _ = srv.wait_for(lambda e: e["type"] == "question")
    srv.api("/api/decide", {"ref": q["ref"], "answer": [0]})
    srv.wait_for(lambda e: e["type"] == "done")
    st, _ = srv.wait_for(lambda e: e["type"] == "status" and not e["tabs"][0]["busy"])
    assert st["tabs"][0]["id"] == tab and status(srv)["busy"] is False


def test_estado_no_pisa_el_id_del_evento(tmp_path):
    # Con ids de sesión de solo dígitos, el navegador creía haber visto un evento enorme y descartaba los siguientes
    code = """
from claude_remoto.agent import global_status
import json
st = global_status()
print(json.dumps(["id" in st, st["tab"] == st["tabs"][0]["id"]]))
"""
    p = run_python(tmp_path, code)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout.strip().splitlines()[-1]) == [False, True]


def test_hub_no_deja_pisar_el_id(tmp_path):
    code = """
from claude_remoto import hub as h
import json
hub = h.Hub()
ev = hub.emit('status', id='99999999', type='otro')
print(json.dumps([ev['id'], ev['type']]))
"""
    p = run_python(tmp_path, code)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout.strip().splitlines()[-1]) == [1, "status"]
