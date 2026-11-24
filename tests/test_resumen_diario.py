"""Resumen del día: conteos por pestaña desde el historial, cuándo se envía y configuración."""

import json

from conftest import run_python


def py(tmp_path, code, **env):
    r = run_python(tmp_path, code, **env)
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines()


def test_armar_resumen(tmp_path):
    out = py(tmp_path, """
import json, time
from claude_remoto import daily, usage
now = time.time()
usage.record(tab="Front", tab_id="f", cost=0.25, usage={"input_tokens": 1000, "output_tokens": 500}, duration=30,
             turns=1, model="m", ok=True, now=now)
ayer = now - 86400 * 2
h = [
  {"type": "user", "tab": "f", "ts": now, "text": "SECRETO del prompt"},
  {"type": "decided", "tab": "f", "ts": now, "label": "✅ Aprobado"},
  {"type": "decided", "tab": "f", "ts": now, "label": "❌ Rechazado"},
  {"type": "decided", "tab": "f", "ts": now, "label": "→ dev"},
  {"type": "changes", "tab": "f", "ts": now, "add": 7, "del": 2, "files": [{"path": "a.py"}, {"path": "b.py"}]},
  {"type": "changes", "tab": "f", "ts": now, "add": 1, "del": 0, "files": [{"path": "a.py"}]},
  {"type": "info", "tab": "f", "ts": now, "text": "⏰ Tarea programada «Pruebas»"},
  {"type": "done", "tab": "f", "ts": now, "ok": True, "duration": 30},
  {"type": "done", "tab": "b", "ts": now, "ok": False, "duration": 10},
  {"type": "error", "tab": "b", "ts": now, "text": "boom"},
  {"type": "done", "tab": "f", "ts": ayer, "ok": True, "duration": 99},     # otro día: no cuenta
]
s = daily.build(h, {"f": "Front", "b": "Backend"}, now)
print(json.dumps(s, ensure_ascii=False))
print(json.dumps(daily.build([], {}, now)["tasks"]))
""")
    s = json.loads(out[0])
    assert (s["tasks"], s["failed"], s["approved"], s["rejected"], s["files"], s["add"], s["del"]) == (3, 2, 1, 1, 2, 8, 2)
    assert s["cost"] == 0.25 and s["tokens"] == 1500 and s["duration"] == 40
    front = next(t for t in s["tabs"] if t["name"] == "Front")
    assert front["scheduled"] == 1 and front["cost"] == 0.25 and front["files"] == 2
    assert [t["name"] for t in s["tabs"]] == ["Backend", "Front"] and s["tabs"][0]["failed"] == 2   # más tareas primero
    assert "SECRETO" not in out[0]                                     # sin textos de las tareas
    assert out[1] == "0"


def test_cuando_se_envia(tmp_path):
    out = py(tmp_path, """
import time
from claude_remoto import daily
t = lambda h, m: time.mktime((2026, 11, 24, h, m, 0, 0, 0, -1))
print(daily.is_due(t(19, 0)))                       # desactivado por defecto
daily.save(True, "18:30")
print(daily.is_due(t(18, 29)), daily.is_due(t(18, 30)), daily.is_due(t(23, 0)))
daily.mark_sent(t(18, 30))
print(daily.is_due(t(20, 0)), daily.is_due(t(18, 30) + 86400))   # una vez al día
print(daily.save(True, "25:00")[1] is not None)
""")
    assert out == ["False", "False True True", "False True", "True"]


def test_activado_por_env(tmp_path):
    out = py(tmp_path, "from claude_remoto import daily; print(daily.settings())", DAILY_SUMMARY_AT="17:45")
    assert out == ["{'enabled': True, 'at': '17:45', 'last': ''}"]


def test_api_y_envio(make_server):
    s = make_server("basic", DAILY_TICK="0.5")
    s.login()
    st, r = s.api("/api/cmd", {"cmd": "daily_get"})
    assert st == 200 and not r["settings"]["enabled"] and r["summary"]["tasks"] == 0
    s.api("/api/send", {"text": "revisa"})
    perm, _ = s.wait_for(lambda e: e["type"] == "perm")
    s.api("/api/decide", {"ref": perm["ref"], "choice": "y"})
    q, _ = s.wait_for(lambda e: e["type"] == "question")
    s.api("/api/decide", {"ref": q["ref"], "answer": [0]})
    s.wait_for(lambda e: e["type"] == "done")
    st, r = s.api("/api/cmd", {"cmd": "daily_get"})
    assert r["summary"]["tasks"] == 1 and r["summary"]["approved"] == 1
    assert s.api("/api/cmd", {"cmd": "daily_set", "enabled": True, "at": "99:00"})[0] == 400
    st, r = s.api("/api/cmd", {"cmd": "daily_set", "enabled": True, "at": "00:00"})   # ya pasó la hora: se envía
    assert st == 200 and r["settings"]["at"] == "00:00"
    ev, _ = s.wait_for(lambda e: e["type"] == "daily_summary", timeout=10)
    assert ev["tasks"] == 1 and ev["approved"] == 1 and "tab" not in ev
    import time
    time.sleep(1.5)
    assert len([e for e in s.events if e["type"] == "daily_summary"]) == 1   # una sola vez al día
