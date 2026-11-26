"""Capa de motores: una pestaña funciona con cualquier motor que cumpla el contrato (no solo Claude Code)."""

import json

from conftest import run_python

OTRO_MOTOR = """
import asyncio, json
from mando.engines import ENGINES, Engine, TurnResult
from mando import agent as ag
from mando.hub import HUB

class Eco(Engine):
    name, label = "eco", "Motor de prueba"
    capabilities = {**Engine.capabilities, "modes": ["default"]}
    def __init__(self, host):
        super().__init__(host); self.closed = 0
    async def run(self, prompt, images=None):
        self.host.stream_start("m1")
        for part in ["Hola, ", "voy a ", "trabajar."]:
            self.host.stream_delta(part)
        self.host.stream_stop()
        self.host.final_text("Hola, voy a trabajar.", "m1")
        ok = await self.host.decide_tool("Bash", {"command": "npm test"})   # aprobada por regla
        bloq = await self.host.pre_tool("Bash", {"command": "sudo rm -rf /"})   # regla de bloqueo
        self.host.tool_used("Bash", {"command": "npm test"})
        self.host.final_text(f"permitido={ok.allow} bloqueo={bool(bloq)}")
        self.host.turn_result(TurnResult(session_id="hilo-1", usage={"input_tokens": 10, "output_tokens": 5}, model="eco-1"))
    async def close(self):
        self.closed += 1

ENGINES["eco"] = Eco
async def main():
    a = ag.Agent({"id": "t9", "name": "Eco", "engine": "eco", "cwd": "/tmp"})
    a.rules.append({"id": "r", "action": "allow", "kind": "bash", "pattern": "npm test*", "note": ""})
    assert a.submit("hola") == "started"
    await a.task
    ev = [e for e in HUB.history if e.get("tab") == "t9"]
    print(json.dumps([e["type"] for e in ev]))
    print(json.dumps([e.get("text") for e in ev if e["type"] == "assistant"], ensure_ascii=False))
    print(a.tab_state()["engine"], a.status()["engine"], a.session_id)
    await a.close(); print(a.engine.closed)
    print(json.dumps(ag.Agent({"id": "x", "engine": "no-existe"}).engine.name))
asyncio.run(main())
"""


def test_otro_motor_cumple_el_contrato(tmp_path):
    r = run_python(tmp_path, OTRO_MOTOR)
    assert r.returncode == 0, r.stderr
    types, texts, state_line, closed, unknown = r.stdout.strip().splitlines()
    types = json.loads(types)
    assert types[0] == "user" and types[-1] == "done" and "tool" in types and "info" in types   # bloqueo informado
    assert json.loads(texts) == ["Hola, voy a trabajar.", "permitido=True bloqueo=True"]
    assert state_line == "eco eco hilo-1"
    assert closed == "1"
    assert json.loads(unknown) == "claude"             # pestaña guardada con un motor que ya no existe


def test_claude_es_el_predeterminado_y_se_guarda(srv):
    st = srv.api("/api/cmd", {"cmd": "status"})[1]["status"]
    assert st["engine"] == "claude" and all(t["engine"] == "claude" for t in st["tabs"])
