"""Costo y uso: registro por tarea (sin el texto) y resúmenes por día, pestaña y modelo."""

import json

from conftest import run_python


def finish_task(s, text="revisa el secreto XYZ"):
    s.api("/api/send", {"text": text})
    perm, _ = s.wait_for(lambda e: e["type"] == "perm")
    s.api("/api/decide", {"ref": perm["ref"], "choice": "y"})
    q, _ = s.wait_for(lambda e: e["type"] == "question")
    s.api("/api/decide", {"ref": q["ref"], "answer": [0]})
    done, _ = s.wait_for(lambda e: e["type"] == "done")
    return done


def test_registro_de_una_tarea(srv, tmp_path):
    done = finish_task(srv)
    assert done["cost"] == 0.0012 and done["tokens"] == 1500
    f = tmp_path / ".mando-usage.jsonl"
    assert oct(f.stat().st_mode)[-3:] == "600"
    raw = f.read_text()
    rec = json.loads(raw.strip().splitlines()[-1])
    assert rec["tab"] == "Principal" and rec["in"] == 1200 and rec["out"] == 300 and rec["cache_read"] == 5000
    assert "XYZ" not in raw                                        # no guarda el texto de la tarea
    st, j = srv.api("/api/cmd", {"cmd": "usage"})
    assert st == 200 and j["totals"]["today"]["tasks"] == 1 and j["totals"]["today"]["cost"] == 0.0012
    assert j["days"][-1]["tasks"] == 1 and len(j["days"]) == 14
    assert j["tabs"][0]["name"] == "Principal"


def test_resumen_por_periodo(tmp_path):
    code = """
import json, time
from mando import usage
now = time.mktime((2026, 11, 23, 12, 0, 0, 0, 0, -1))
def rec(days, tab, model, cost, tin):
    usage.record(tab=tab, tab_id=tab, cost=cost, usage={"input_tokens": tin, "output_tokens": 10},
                 duration=5, turns=1, model=model, ok=True, now=now - days * 86400)
rec(0, "A", "opus", 0.50, 100); rec(0, "B", "sonnet", 0.10, 100)
rec(3, "A", "opus", 1.00, 100); rec(20, "B", "sonnet", 2.00, 100); rec(40, "A", "opus", 9.00, 100)
s = usage.summary(now=now)
print(json.dumps([s["totals"]["today"]["cost"], s["totals"]["week"]["cost"], s["totals"]["month"]["cost"],
                  s["totals"]["month"]["tasks"], [t["name"] for t in s["tabs"]], [m["name"] for m in s["models"]],
                  s["days"][-1]["tasks"], s["days"][-4]["cost"]]))
"""
    p = run_python(tmp_path, code)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert out == [0.6, 1.6, 3.6, 4, ["B", "A"], ["sonnet", "opus"], 2, 1.0]   # lo de hace 40 días no cuenta


def test_costo_acumulado_se_registra_por_tarea(srv, tmp_path):
    """El CLI informa el total acumulado de la sesión: se registra solo lo de cada tarea."""
    finish_task(srv)
    done = finish_task(srv)                       # el CLI informa 0.0024 (acumulado): la tarea costó 0.0012
    assert done["cost"] == 0.0012
    _, j = srv.api("/api/cmd", {"cmd": "usage"})
    assert j["totals"]["today"]["tasks"] == 2 and j["totals"]["today"]["cost"] == 0.0024


def test_costo_por_turno_con_total_acumulado(tmp_path):
    code = """
import json
from mando.agent import AGENTS
from mando.engines import TurnResult
a = AGENTS.first()
c = lambda cost, sid="s1": a.turn_cost(TurnResult(session_id=sid, cost=cost, cost_cumulative=True))
out = [c(1.0), c(1.5), c(4.0), c(0.3),        # 0.3 < 4.0: /clear o sesión retomada sin total → todo es nuevo
       c(2.0, "s2"),                           # otra sesión parte de cero
       a.turn_cost(TurnResult(cost=0.7))]      # un motor que informa el costo del turno queda igual
AGENTS.persist()
print(json.dumps([out, a.tab_state()["cost_seen"]]))
"""
    p = run_python(tmp_path, code)
    assert p.returncode == 0, p.stderr
    out, seen = json.loads(p.stdout.strip().splitlines()[-1])
    assert out == [1.0, 0.5, 2.5, 0.3, 2.0, 0.7]
    assert seen == {"s1": 0.3, "s2": 2.0}


def test_corrige_registro_anterior_acumulado(tmp_path):
    f = tmp_path / ".mando-usage.jsonl"
    rows = [{"ts": 1, "tab_id": "A", "cost": 1.0}, {"ts": 2, "tab_id": "B", "cost": 0.5},
            {"ts": 3, "tab_id": "A", "cost": 1.5}, {"ts": 4, "tab_id": "A", "cost": 4.0},
            {"ts": 5, "tab_id": "A", "cost": 0.2}, {"ts": 6, "tab_id": "B", "cost": 0.75}]
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\nbasura\n")
    code = """
import json
from mando import usage
print(json.dumps([usage.fix_cumulative(), usage.fix_cumulative()]))
"""
    p = run_python(tmp_path, code)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout.strip().splitlines()[-1]) == [3, 0]          # la segunda vez no toca nada
    lines = f.read_text().splitlines()
    recs = [json.loads(x) for x in lines if x != "basura"]
    assert [r["cost"] for r in recs] == [1.0, 0.5, 0.5, 2.5, 0.2, 0.25] and all(r["v"] == 2 for r in recs)
    assert "basura" in lines                                                  # no pierde líneas
    assert (tmp_path / ".mando-usage.jsonl.bak").read_text().count("\n") == 7  # copia del original
