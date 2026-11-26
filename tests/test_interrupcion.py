"""Detener una tarea: sin tarjetas huérfanas aunque llegue una pregunta justo al interrumpir."""

import time

import pytest
from conftest import run_python


def test_pregunta_que_llega_al_detener_no_queda_abierta(make_server):
    s = make_server("detener_pregunta")
    s.login()
    s.api("/api/send", {"text": "algo largo"})
    s.wait_for(lambda e: e["type"] == "assistant")
    assert s.api("/api/cmd", {"cmd": "stop"})[0] == 200
    s.wait_for(lambda e: e["type"] == "info" and "Interrumpido" in e.get("text", ""))
    time.sleep(1)
    with pytest.raises(AssertionError, match="no llegó"):
        s.wait_for(lambda e: e["type"] == "question", timeout=2)       # ya no aparece la tarjeta
    st = s.api("/api/cmd", {"cmd": "status"})[1]["status"]
    assert st["pending"] == 0 and not st["busy"]
    assert "FAKE interrupt" in s.log.read_text()


def test_decision_cancelada_se_cierra_en_todos_lados(tmp_path):
    r = run_python(tmp_path, """
import asyncio
from mando.decisions import PENDING, Pending, wait_decision
from mando.hub import HUB
async def main():
    PENDING["p1"] = Pending(future=asyncio.get_running_loop().create_future(), kind="question", tab="t1")
    t = asyncio.create_task(wait_decision("p1"))
    await asyncio.sleep(0.05)
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass
    ev = [e for e in HUB.history if e["type"] == "decided"]
    print(ev[-1]["ref"], ev[-1]["label"], ev[-1]["tab"], "p1" in PENDING)
asyncio.run(main())
""")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "p1 ⏹️ Cancelado t1 False"


def test_despues_de_detener_la_siguiente_tarea_pide_permisos(make_server):
    s = make_server("basic")
    s.login()
    s.api("/api/send", {"text": "una"})
    s.wait_for(lambda e: e["type"] == "perm")
    s.api("/api/cmd", {"cmd": "stop"})
    s.wait_for(lambda e: e["type"] == "decided" and "Cancelado" in e.get("label", ""))
    for _ in range(50):
        if not s.api("/api/cmd", {"cmd": "status"})[1]["status"]["busy"]:
            break
        time.sleep(0.1)
    s.api("/api/send", {"text": "otra"})
    perm, _ = s.wait_for(lambda e: e["type"] == "perm")                 # «detener» no afecta a la próxima
    assert perm["title"] == "💻 Ejecutar comando"
