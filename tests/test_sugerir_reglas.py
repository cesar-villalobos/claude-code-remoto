"""Sugerir reglas tras aprobar varias veces lo mismo, sin sugerir nunca algo riesgoso."""

import json

from conftest import run_python


def py(tmp_path, code, **env):
    r = run_python(tmp_path, code, **env)
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines()


def test_que_se_sugiere_y_que_nunca(tmp_path):
    out = py(tmp_path, """
import json
from claude_remoto.agent import shared_rules
from claude_remoto.rule_suggest import candidate
rules = shared_rules()
def c(cmd): r = candidate("Bash", {"command": cmd}, "/tmp", rules); return r and r["pattern"]
ok = ["npm test", "pytest -q tests", "ls -la", "git status", "git diff --stat", "make build", "python manage.py test"]
bad = ["rm -rf build", "sudo ls", "git push origin main", "git reset --hard", "npm install left-pad", "pip install x",
       "python -c 'print(1)'", "curl https://x", "npm test && rm x", "cat ~/.ssh/id_rsa", "bash -c 'x'", "docker run x",
       "chmod 777 x", "mv a b", "", "echo $HOME"]
print(json.dumps([c(x) for x in ok]))
print(json.dumps([c(x) for x in bad]))
print(json.dumps(candidate("mcp__jira__get_issue", {"key": "A-1"}, "/tmp", rules)))
print(json.dumps(candidate("Edit", {"file_path": "/tmp/a.py"}, "/tmp", rules)))
rules.append({"id": "x", "action": "allow", "kind": "bash", "pattern": "npm test*", "note": ""})
print(json.dumps(c("npm test")))
""")
    assert json.loads(out[0]) == ["npm test*", "pytest -q*", "ls -la*", "git status*", "git diff*", "make build*",
                                  "python manage.py*"]
    assert json.loads(out[1]) == [None] * 16
    assert json.loads(out[2]) == {"kind": "mcp", "pattern": "mcp__jira__get_issue"}
    assert json.loads(out[3]) is None                         # ediciones: no se sugieren
    assert json.loads(out[4]) is None                         # ya hay una regla


def test_umbral_ahora_no_y_nunca(tmp_path):
    out = py(tmp_path, """
from claude_remoto.agent import shared_rules
from claude_remoto import rule_suggest as rs
rules = shared_rules()
a = lambda: rs.on_approved("Bash", {"command": "npm test"}, "/tmp", rules)
print(a() is None, (o := a())["pattern"], o["count"])
print(a() is None)                                             # ya ofrecida y sin responder: no se repite
rs.take(o["ref"])                                              # «Ahora no»
print(a() is None, a() is not None)                            # se vuelve a ofrecer tras otras 2
o2 = [v for v in rs.OFFERS.items()][0]
rs.dismiss_forever(rs.take(o2[0]))                             # «No volver a sugerir»
print(all(a() is None for _ in range(5)))
""")
    assert out == ["True npm test* 2", "True", "True True", "True"]


def test_desactivado(tmp_path):
    out = py(tmp_path, """
from claude_remoto.agent import shared_rules
from claude_remoto import rule_suggest as rs
print([rs.on_approved("Bash", {"command": "npm test"}, "/tmp", shared_rules()) for _ in range(4)])
""", RULE_SUGGEST_AFTER="0")
    assert out == ["[None, None, None, None]"]


def run_task(s):
    s.api("/api/send", {"text": "corre las pruebas"})
    ev, seen = s.wait_for(lambda e: e["type"] in ("perm", "question"))
    if ev["type"] == "perm":
        s.api("/api/decide", {"ref": ev["ref"], "choice": "y"})
        ev, seen = s.wait_for(lambda e: e["type"] == "question")
    s.api("/api/decide", {"ref": ev["ref"], "answer": [0]})
    _, seen = s.wait_for(lambda e: e["type"] == "done")
    return seen


def test_flujo_completo(srv):
    seen = run_task(srv)
    assert not [e for e in seen if e["type"] == "rule_suggest"]
    seen = run_task(srv)                                           # segunda aprobación de «pytest -q»
    offer = [e for e in seen if e["type"] == "rule_suggest"]
    assert len(offer) == 1 and offer[0]["pattern"] == "pytest -q*" and offer[0]["tab"]
    st, r = srv.api("/api/cmd", {"cmd": "rule_suggest_accept", "arg": offer[0]["ref"]})
    assert st == 200
    done, _ = srv.wait_for(lambda e: e["type"] == "rule_suggest_done")
    assert "Regla creada" in done["label"]
    rules = srv.api("/api/cmd", {"cmd": "rules"})[1]["rules"]
    assert any(x["pattern"] == "pytest -q*" and x["action"] == "allow" and x["note"] == "sugerida" for x in rules)
    assert srv.api("/api/cmd", {"cmd": "rule_suggest_accept", "arg": offer[0]["ref"]})[0] == 404   # ya respondida
    seen = run_task(srv)                                           # la tercera vez se aprueba sola
    assert any(e["type"] == "info" and "Aprobado por regla" in e.get("text", "") for e in seen)
    _, body = srv.api("/api/cmd", {"cmd": "audit", "q": "sugerencia"})
    assert body["events"][0]["event"] == "rule_add"
