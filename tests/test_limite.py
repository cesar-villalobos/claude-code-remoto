"""Límite de gasto mensual: aviso, pausa de tareas nuevas (también las en cola), continuar y validación."""

import json
import time

from conftest import run_python
from test_costo import finish_task


def server_with_spent(make_server, tmp_path, spent, **env):
    home = tmp_path / "casa"
    home.mkdir()
    now = time.time()
    with (home / ".claude-remoto-usage.jsonl").open("w") as f:
        f.write(json.dumps({"ts": now - 5, "tab": "Principal", "cost": spent, "in": 1, "out": 1}) + "\n")
    s = make_server(HOME=str(home), STATE_FILE=str(home / "state.json"), CLAUDE_WORKDIR=str(home),
                    ALLOWED_ROOTS=str(home), **env)
    s.login()
    return s


def budget(s):
    return s.api("/api/cmd", {"cmd": "status"})[1]["status"]["budget"]


def test_sin_limite_por_defecto(srv):
    b = budget(srv)
    assert b["level"] == "off" and not b["blocked"] and b["limit"] == 0
    assert srv.api("/api/send", {"text": "hola"})[0] == 200


def test_aviso_y_pausa_al_llegar_al_limite(make_server, tmp_path):
    s = server_with_spent(make_server, tmp_path, 0.0045, SPEND_LIMIT_USD="0.005")
    b = budget(s)
    assert b["level"] == "warn" and b["pct"] == 90.0 and not b["blocked"]
    done = finish_task(s)                                           # +0.0012 → supera el límite
    assert done["ok"]
    note, _ = s.wait_for(lambda e: e["type"] == "info" and "límite de gasto mensual" in e.get("text", ""))
    assert "tab" not in note                                        # aviso en todas las pestañas
    b = budget(s)
    assert b["level"] == "over" and b["blocked"] and b["spent"] == 0.0057
    st, r = s.api("/api/send", {"text": "otra tarea secreta"})
    assert st == 402 and "límite" in r["error"] and r["budget"]["blocked"]
    _, body = s.api("/api/cmd", {"cmd": "audit", "q": "límite"})
    ev = body["events"][0]
    assert ev["event"] == "task_blocked" and "secreta" not in json.dumps(ev)
    # continuar este mes
    st, r = s.api("/api/cmd", {"cmd": "budget_override"})
    assert st == 200 and r["budget"]["override"] and not r["budget"]["blocked"]
    assert s.api("/api/send", {"text": "sigo"})[0] == 200


def test_la_cola_no_sigue_al_llegar_al_limite(make_server, tmp_path):
    s = server_with_spent(make_server, tmp_path, 0.004, SPEND_LIMIT_USD="0.005")
    s.api("/api/send", {"text": "primera"})
    perm, _ = s.wait_for(lambda e: e["type"] == "perm")
    assert s.api("/api/send", {"text": "en cola"})[1]["queued"] is True    # aún bajo el límite: se encola
    s.api("/api/decide", {"ref": perm["ref"], "choice": "y"})
    q, _ = s.wait_for(lambda e: e["type"] == "question")
    s.api("/api/decide", {"ref": q["ref"], "answer": [0]})
    s.wait_for(lambda e: e["type"] == "done")
    ev, seen = s.wait_for(lambda e: e["type"] == "unqueued")
    assert "límite de gasto" in ev["label"]
    assert not [e for e in seen if e["type"] == "user" and e.get("text") == "en cola"]


def test_configurar_limite(srv):
    for bad in ({"limit": -1}, {"limit": "abc"}, {"limit": 10, "warn": 30}, {"limit": 1e9}):
        assert srv.api("/api/cmd", {"cmd": "budget_set", **bad})[0] == 400
    st, r = srv.api("/api/cmd", {"cmd": "budget_set", "limit": 25, "warn": 90})
    assert st == 200 and r["budget"]["limit"] == 25 and r["budget"]["warn"] == 90 and r["budget"]["level"] == "ok"
    assert budget(srv)["limit"] == 25                               # queda en el estado
    _, body = srv.api("/api/cmd", {"cmd": "audit", "q": "límite"})
    assert body["events"][0]["event"] == "budget_set"
    b = srv.api("/api/cmd", {"cmd": "backup_export"})[1]["backup"]
    assert b["budget"] == {"limit": 25.0, "warn": 90}
    st, r = srv.api("/api/cmd", {"cmd": "budget_set", "limit": 0})
    assert r["budget"]["level"] == "off"


def test_solo_cuenta_el_mes_en_curso(tmp_path):
    code = """
import time
from claude_remoto import budget, usage
now = time.mktime((2026, 11, 3, 12, 0, 0, 0, 0, -1))
usage.record(tab="a", tab_id="a", cost=7, usage={}, duration=1, turns=1, model="m", ok=True, now=now - 5 * 86400)  # octubre
usage.record(tab="a", tab_id="a", cost=2, usage={}, duration=1, turns=1, model="m", ok=True, now=now)
print(usage.month_cost(now))
before = {"level": "ok", "month": "2026-11"}
print(budget.crossed(before, {"level": "warn", "month": "2026-11", "spent": 8, "limit": 10, "pct": 80, "override": False}))
print(budget.crossed({"level": "warn", "month": "2026-11"}, {"level": "warn", "month": "2026-11"}))
"""
    r = run_python(tmp_path, code)
    assert r.returncode == 0, r.stderr
    out = r.stdout.splitlines()
    assert out[0] == "2.0"
    assert "80 %" in out[1] and out[2] == "None"
