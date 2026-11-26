"""Lógica interna sin red: filtro de IPs, límites de login, historial y validaciones."""

import json

from conftest import run_python


def _json(proc):
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


IPS = ["192.168.1.20", "10.0.0.5", "172.20.1.1", "127.0.0.1", "8.8.8.8", "181.161.4.9",
       "100.64.0.1", "2001:4860::8888", "::ffff:192.168.1.3"]
CHECK = f"from mando import auth as s; import json; print(json.dumps({{ip: s.ip_allowed(ip) for ip in {IPS!r}}}))"


def test_solo_red_local_por_defecto(tmp_path):
    r = _json(run_python(tmp_path, CHECK))
    assert all(r[ip] for ip in ["192.168.1.20", "10.0.0.5", "172.20.1.1", "127.0.0.1", "::ffff:192.168.1.3"])
    assert not any(r[ip] for ip in ["8.8.8.8", "181.161.4.9", "100.64.0.1", "2001:4860::8888"])


def test_ips_publicas_rechazadas_aunque_se_configure_publico(tmp_path):
    # El modo público ya no existe: PUBLIC_URL se ignora y las IPs públicas siguen fuera
    r = _json(run_python(tmp_path, CHECK, PUBLIC_URL="https://203.0.113.5:8443"))
    assert not r["8.8.8.8"] and not r["2001:4860::8888"] and r["192.168.1.20"]


def test_limite_de_login_por_ip_y_global(tmp_path):
    code = """
from mando import auth as s
import json
out = {}
for i in range(10): s.record_failure('203.0.113.9')
s.GLOBAL_FAILS.clear()
out['ip_bloqueada'] = s.too_many_failures('203.0.113.9')
out['otra_ip_libre'] = s.too_many_failures('203.0.113.10')
s.FAILED_LOGINS.clear(); s.GLOBAL_FAILS.clear()
for i in range(29): s.record_failure(f'198.51.100.{i}')
out['29_fallos'] = s.too_many_failures('203.0.113.77')
s.record_failure('198.51.100.200')
out['30_fallos'] = s.too_many_failures('203.0.113.77')
print(json.dumps(out))
"""
    r = _json(run_python(tmp_path, code))
    assert r == {"ip_bloqueada": True, "otra_ip_libre": False, "29_fallos": False, "30_fallos": True}


def test_historial_recorta_y_pide_snapshot(tmp_path):
    code = """
from mando import hub as s
import json
s.HISTORY_MAX = 5
h = s.Hub()
for i in range(8): h.emit('info', text=str(i))
h.emit('status', busy=False)          # status no se guarda en el historial
print(json.dumps({
  'largo': len(h.history), 'primero': h.history[0]['text'], 'seq': h.seq,
  'desde_6': [e['text'] for e in h.since(6)],
  'cursor_viejo': h.since(1), 'cursor_futuro': h.since(99),
}))
"""
    r = _json(run_python(tmp_path, code))
    assert r["largo"] == 5 and r["primero"] == "3" and r["seq"] == 9
    assert r["desde_6"] == ["6", "7"]
    assert r["cursor_viejo"] is None and r["cursor_futuro"] is None  # ⇒ snapshot completo


def test_opcion_de_tiempo_real_hacia_el_sdk(tmp_path):
    code = "from mando import agent as s; import json; print(json.dumps(s.Agent({'id': 't'}).options().include_partial_messages))"
    assert _json(run_python(tmp_path, code)) is True                      # activado por defecto
    assert _json(run_python(tmp_path, code, STREAM_TEXT="0")) is False    # se puede desactivar


DIFF_CODE = """
from mando import diff as s
import json, pathlib
d = pathlib.Path({tmp!r})
f = d / "a.py"
f.write_text("".join(f"x{{i}}\\n" for i in range(1, 21)))
out = {{}}
e = s.edit_diff("Edit", {{"file_path": str(f), "old_string": "x10", "new_string": "x10b"}}, str(d))
out["edit"] = e
out["edit_rel"] = s.edit_diff("Edit", {{"file_path": "a.py", "old_string": "x3", "new_string": "y3"}}, str(d))["numbered"]
out["fallback"] = s.edit_diff("Edit", {{"file_path": str(f), "old_string": "no existe", "new_string": "z"}}, str(d))
out["multi"] = s.edit_diff("MultiEdit", {{"file_path": str(f), "edits": [
    {{"old_string": "x2", "new_string": "dos"}}, {{"old_string": "x19", "new_string": "diecinueve"}}]}}, str(d))
out["write_new"] = s.edit_diff("Write", {{"file_path": str(d / "n.py"), "content": "a\\nb\\n"}}, str(d))
out["write_old"] = s.edit_diff("Write", {{"file_path": str(f), "content": "solo\\n"}}, str(d))
(d / "bin.dat").write_bytes(bytes(range(256)))
out["binario"] = s.edit_diff("Write", {{"file_path": str(d / "bin.dat"), "content": "x"}}, str(d))
big = s.build_diff("", "".join(f"l{{i}}\\n" for i in range(1000)), numbered=True)
out["big"] = [len(big["lines"]), big["truncated"], big["add"]]
print(json.dumps(out))
"""


def test_diff_de_ediciones(tmp_path):
    r = _json(run_python(tmp_path, DIFF_CODE.format(tmp=str(tmp_path))))
    e = r["edit"]
    assert e["numbered"] and e["add"] == 1 and e["del"] == 1 and not e["new_file"]
    hunk, *rest = e["lines"]
    assert hunk == {"t": "hunk", "o": 7, "n": 7}                     # 3 líneas de contexto antes
    assert {"t": "del", "o": 10, "s": "x10"} in rest and {"t": "add", "n": 10, "s": "x10b"} in rest
    assert [l["t"] for l in rest].count("ctx") == 6                  # 3 antes + 3 después
    assert r["edit_rel"] is True                                     # ruta relativa a la carpeta de trabajo
    fb = r["fallback"]                                               # fragmento que no está en el archivo
    assert fb["numbered"] is False and fb["add"] == 1 and fb["del"] == 1
    m = r["multi"]
    assert m["numbered"] and m["add"] == 2 and m["del"] == 2 and [l["t"] for l in m["lines"]].count("hunk") == 2
    assert r["write_new"]["new_file"] and r["write_new"]["add"] == 2 and r["write_new"]["del"] == 0
    assert r["write_old"]["del"] == 20 and r["write_old"]["add"] == 1
    assert r["binario"] is None                                      # no se intenta mostrar binarios
    assert r["big"] == [400, True, 1000]                             # se recorta lo enviado, no el conteo


def test_modo_inicial_desde_la_configuracion(tmp_path):
    code = "from mando import agent as s; import json; print(json.dumps(s.Agent({'id': 't'}).options().permission_mode))"
    assert _json(run_python(tmp_path, code)) == "default"
    assert _json(run_python(tmp_path, code, PERMISSION_MODE="plan")) == "plan"
    assert _json(run_python(tmp_path, code, PERMISSION_MODE="bypassPermissions")) == "default"  # no permitido


RULES_CODE = """
from mando import rules as s
from mando.agent import Agent
import json
rules = [
  {"id": "1", "action": "deny", "kind": "bash", "pattern": "*rm -rf*"},
  {"id": "2", "action": "allow", "kind": "bash", "pattern": "npm test*"},
  {"id": "3", "action": "deny", "kind": "edit", "pattern": "*.env"},
  {"id": "4", "action": "allow", "kind": "edit", "pattern": "docs/*"},
  {"id": "5", "action": "allow", "kind": "mcp", "pattern": "mcp__atlassian__get*"},
]
cwd = "/proy"
def f(action, tool, data):
    r = s.find_rule(rules, action, tool, data, cwd)
    return r["id"] if r else None
out = {
  "rm": f("deny", "Bash", {"command": "cd build && rm -rf dist"}),
  "rm_nl": f("deny", "Bash", {"command": "echo hola\\nrm -rf /"}),
  "npm": f("allow", "Bash", {"command": "npm test -- --watch=false"}),
  "npm_and": f("allow", "Bash", {"command": "npm test && rm x"}),
  "npm_pipe": f("allow", "Bash", {"command": "npm test | tee log"}),
  "npm_sub": f("allow", "Bash", {"command": "npm test $(whoami)"}),
  "npx": f("allow", "Bash", {"command": "npx jest"}),
  "env_abs": f("deny", "Write", {"file_path": "/proy/config/.env"}),
  "env_rel": f("deny", "Edit", {"file_path": ".env"}),
  "docs_abs": f("allow", "Edit", {"file_path": "/proy/docs/guia.md"}),
  "docs_otro": f("allow", "Edit", {"file_path": "/otro/docs/guia.md"}),
  "edit_no_es_bash": f("deny", "Edit", {"file_path": "rm -rf.txt"}),
  "jira_get": f("allow", "mcp__atlassian__getJiraIssue", {}),
  "jira_create": f("allow", "mcp__atlassian__createJiraIssue", {}),
  "sugerencia": s.suggest_rule("Bash", {"command": "git status --short"}, cwd),
  "sug_edit": s.suggest_rule("Edit", {"file_path": "/proy/src/a.py"}, cwd),
  "val_todo": s.validate_rule({"action": "allow", "kind": "bash", "pattern": "**"})[1],
  "val_tipo": s.validate_rule({"action": "allow", "kind": "otro", "pattern": "x"})[1],
  "val_ok": s.validate_rule({"action": "deny", "kind": "bash", "pattern": "*shutdown*"})[1],
  "defaults": [r["pattern"] for r in Agent({"id": "t"}).rules],
}
print(json.dumps(out))
"""


def test_motor_de_reglas(tmp_path):
    r = _json(run_python(tmp_path, RULES_CODE))
    assert r["rm"] == "1" and r["rm_nl"] == "1"                       # bloquea aunque esté encadenado
    assert r["npm"] == "2"
    assert r["npm_and"] is None and r["npm_pipe"] is None and r["npm_sub"] is None  # nunca aprueba encadenados
    assert r["npx"] is None
    assert r["env_abs"] == "3" and r["env_rel"] == "3"
    assert r["docs_abs"] == "4" and r["docs_otro"] is None           # relativo a la carpeta de trabajo
    assert r["edit_no_es_bash"] is None                               # el tipo debe coincidir
    assert r["jira_get"] == "5" and r["jira_create"] is None
    assert r["sugerencia"] == {"kind": "bash", "pattern": "git status*"}
    assert r["sug_edit"] == {"kind": "edit", "pattern": "src/a.py"}
    assert "aprobaría todo" in r["val_todo"] and r["val_tipo"] == "Tipo inválido" and r["val_ok"] is None
    assert "*rm -rf*" in r["defaults"] and "*.env" in r["defaults"] and "sudo *" in r["defaults"]


MCP_CODE = "from mando import config, decisions as d; import json; print(json.dumps([config.MCP_SERVERS, d.tool_view('mcp__docs__buscar', {'q': 'x'})[0]]))"


def test_servidores_mcp_configurables(tmp_path):
    r = _json(run_python(tmp_path, MCP_CODE))
    assert r == [{}, "🧩 docs · buscar"]                       # ninguno fijo; tarjeta genérica
    r = _json(run_python(tmp_path, MCP_CODE, MCP_SERVERS='{"docs": {"type": "http", "url": "https://mcp.ejemplo.com/mcp"}}'))
    assert r[0] == {"docs": {"type": "http", "url": "https://mcp.ejemplo.com/mcp"}}
    r = _json(run_python(tmp_path, MCP_CODE, ATLASSIAN_MCP_URL="https://mcp.ejemplo.com/v2/mcp"))   # compatibilidad
    assert r[0] == {"atlassian": {"type": "http", "url": "https://mcp.ejemplo.com/v2/mcp"}}
    p = run_python(tmp_path, MCP_CODE, MCP_SERVERS="no es json")
    assert p.returncode != 0 and "MCP_SERVERS" in p.stderr
