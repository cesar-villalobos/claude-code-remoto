"""Tareas programadas: cálculo de la próxima ejecución, validación, ejecución y ejecuciones perdidas."""

import datetime as dt
import json
import time

from conftest import run_python


def py(tmp_path, code, **env):
    r = run_python(tmp_path, code, **env)
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines()


def test_proxima_ejecucion(tmp_path):
    out = py(tmp_path, """
import datetime as dt, json
from claude_remoto.schedules import next_run, describe
t = lambda *a: dt.datetime(*a).timestamp()
f = lambda ts: dt.datetime.fromtimestamp(ts).strftime("%a %d %H:%M") if ts else None
now = t(2026, 11, 24, 7, 0)                                  # martes 07:00
lv = {"kind": "repeat", "time": "08:00", "days": [1, 2, 3, 4, 5], "enabled": True}
print(json.dumps([f(next_run(lv, now)), f(next_run(lv, t(2026, 11, 24, 8, 0))), f(next_run(lv, t(2026, 11, 27, 9, 0))),
                  f(next_run({**lv, "days": [6]}, now)), f(next_run({**lv, "enabled": False}, now)),
                  f(next_run({"kind": "once", "at": "2026-11-25T10:30", "enabled": True}, now)),
                  f(next_run({"kind": "once", "at": "2026-11-23T10:30", "enabled": True}, now))]))
print(json.dumps([describe(lv), describe({**lv, "days": [1, 2, 3, 4, 5, 6, 7]}), describe({**lv, "days": [1, 3]}),
                  describe({"kind": "once", "at": "2026-11-25T10:30"})]))
""")
    assert json.loads(out[0]) == ["Tue 24 08:00", "Wed 25 08:00", "Mon 30 08:00", "Sat 28 08:00", None,
                                  "Wed 25 10:30", None]
    assert json.loads(out[1]) == ["lun a vie a las 08:00", "todos los días a las 08:00", "lun, mié a las 08:00",
                                  "una vez, 25-11-2026 10:30"]


def test_validacion(srv):
    tab = srv.api("/api/cmd", {"cmd": "status"})[1]["status"]["tab"]
    base = {"cmd": "sched_save", "name": "Pruebas", "target": tab, "text": "corre las pruebas", "kind": "repeat",
            "time": "08:00", "days": [1, 2, 3, 4, 5]}
    for bad in ({"name": ""}, {"text": ""}, {"target": "x"}, {"time": "25:00"}, {"days": []}, {"days": [8]},
                {"kind": "nunca"}, {"kind": "once", "at": "2020-01-01T08:00"}, {"kind": "once", "at": "mañana"},
                {"text": "x" * 5001}):
        assert srv.api("/api/cmd", {**base, **bad})[0] == 400, bad
    st, r = srv.api("/api/cmd", base)
    assert st == 200 and r["item"]["summary"] == "lun a vie a las 08:00" and r["item"]["next"] > time.time()
    sid = r["item"]["id"]
    items = srv.api("/api/cmd", {"cmd": "sched_toggle", "arg": sid})[1]["items"]
    assert items[0]["enabled"] is False and items[0]["next"] is None
    assert srv.api("/api/cmd", {"cmd": "sched_delete", "arg": sid})[1]["items"] == []
    _, body = srv.api("/api/cmd", {"cmd": "audit", "q": "programada"})
    assert [e["event"] for e in body["events"][:3]] == ["sched_delete", "sched_toggle", "sched_save"]


def test_se_ejecuta_a_su_hora(make_server):
    s = make_server("basic", SCHED_TICK="0.5")
    s.login()
    tab = s.api("/api/cmd", {"cmd": "status"})[1]["status"]["tab"]
    at = dt.datetime.now().strftime("%Y-%m-%dT%H:%M")                    # este mismo minuto
    st, r = s.api("/api/cmd", {"cmd": "sched_save", "name": "Ahora", "target": tab, "text": "revisa ABC-123",
                               "kind": "once", "at": at})
    assert st == 200, r
    info, _ = s.wait_for(lambda e: e["type"] == "info" and "Tarea programada «Ahora»" in e.get("text", ""), timeout=10)
    user, _ = s.wait_for(lambda e: e["type"] == "user")
    assert user["text"] == "revisa ABC-123" and user["tab"] == tab
    s.wait_for(lambda e: e["type"] == "perm")                             # los permisos siguen pidiendo aprobación
    item = s.api("/api/cmd", {"cmd": "sched_list"})[1]["items"][0]
    assert item["enabled"] is False and item["last_run"]                  # «una vez»: ya se usó
    _, body = s.api("/api/cmd", {"cmd": "audit", "q": "programada"})
    assert any(e["event"] == "task" and e.get("schedule") == "Ahora" for e in body["events"])


def test_ejecutar_ahora_y_cola(srv):
    tab = srv.api("/api/cmd", {"cmd": "status"})[1]["status"]["tab"]
    sid = srv.api("/api/cmd", {"cmd": "sched_save", "name": "Manual", "target": tab, "text": "hola", "kind": "repeat",
                               "time": "03:00", "days": [7]})[1]["item"]["id"]
    assert srv.api("/api/cmd", {"cmd": "sched_run", "arg": sid})[1]["result"] == "started"
    srv.wait_for(lambda e: e["type"] == "perm")
    assert srv.api("/api/cmd", {"cmd": "sched_run", "arg": sid})[1]["result"] == "queued"   # trabajando: a la cola


def test_ejecucion_perdida(tmp_path):
    out = py(tmp_path, """
import asyncio, time, json
from claude_remoto import schedules as sc
now = time.time()
sc.items().append({"id": "a", "name": "Perdida", "tab": "t", "text": "x", "kind": "repeat", "enabled": True,
                   "time": time.strftime("%H:%M", time.localtime(now - 600)), "days": [1, 2, 3, 4, 5, 6, 7],
                   "created": now - 7200, "last_run": None})
calls = []
async def run(s, missed): calls.append([s["name"], missed])
print(asyncio.run(sc.tick(run, now)), json.dumps(calls))
print(asyncio.run(sc.tick(run, now + 30)))                    # ya marcada: no se repite
""")
    assert out[0] == '1 [["Perdida", true]]' and out[1] == "0"
