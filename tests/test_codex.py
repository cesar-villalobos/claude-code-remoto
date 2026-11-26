"""Motor Codex (experimental) contra un `codex app-server` simulado (tests/fake_codex.py)."""

import json
import sys
import time

import pytest
from conftest import ROOT, run_python


def codex_server(make_server, tmp_path, scenario="basic", cwd=None, **env):
    log = tmp_path / "codex-calls.jsonl"
    s = make_server("basic", CODEX_CMD=f"{sys.executable} {ROOT / 'tests' / 'fake_codex.py'}",
                    FAKE_CODEX=scenario, FAKE_CODEX_LOG=str(log), **env)
    s.login()
    s.calls = lambda: [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    st, r = s.api("/api/cmd", {"cmd": "tab_new", "name": "Codex", "engine": "codex", "cwd": str(cwd or tmp_path)})
    assert st == 200, r
    s.tab = r["tab"]
    return s


def approve_all(s, until="done", seen_cards=None):
    while True:
        ev, _ = s.wait_for(lambda e: e.get("tab") == s.tab and e["type"] in ("perm", "question", until, "error"),
                           timeout=20)
        if ev["type"] in (until, "error"):
            return ev
        if seen_cards is not None:
            seen_cards.append(ev)
        s.api("/api/decide", {"ref": ev["ref"], "choice": "y"})


def test_disponible_y_turno_completo(make_server, tmp_path):
    (tmp_path / "app.py").write_text("def f():\n    return 1\n")
    s = codex_server(make_server, tmp_path)
    st = s.api("/api/cmd", {"cmd": "status", "tab": s.tab})[1]["status"]
    assert {e["name"] for e in st["engines"]} == {"claude", "codex"} and st["engine"] == "codex"
    s.api("/api/send", {"text": "arregla f", "tab": s.tab})
    cards = []
    done = approve_all(s, seen_cards=cards)
    assert [c["title"] for c in cards] == ["💻 Ejecutar comando", "✏️ Cambiar · app.py"]
    assert cards[0]["detail"] == "npm test"                               # sin el «bash -lc» de Codex
    assert cards[1]["diff"]["add"] == 1 and cards[1]["diff"]["del"] == 1 and cards[1]["diff"]["numbered"]
    texts = [e["text"] for e in s.events if e["type"] == "assistant" and e.get("tab") == s.tab]
    assert texts == ["Reviso el proyecto con Codex.", "comando=accept archivos=accept"]
    assert done["ok"] is True and done["tokens"] == 1020 and not done.get("cost")
    assert (tmp_path / "app.py").read_text().endswith("return 2\n")
    start = next(c for c in s.calls() if c["method"] == "thread/start")["params"]
    assert start == {"cwd": str(tmp_path), "approvalPolicy": "untrusted", "sandbox": "workspace-write"}
    st = s.api("/api/cmd", {"cmd": "status", "tab": s.tab})[1]["status"]
    assert st["session"] == "th-1" and not st["busy"]
    # segundo turno en la misma conversación (sin abrir otra)
    s.api("/api/send", {"text": "otra cosa", "tab": s.tab})
    approve_all(s)
    assert [c["method"] for c in s.calls()].count("thread/start") == 1


def test_reglas_de_mando_aplican_a_codex(make_server, tmp_path):
    (tmp_path / "app.py").write_text("def f():\n    return 1\n")
    s = codex_server(make_server, tmp_path)
    s.api("/api/cmd", {"cmd": "rule_add", "action": "allow", "kind": "bash", "pattern": "npm test*"})
    s.api("/api/send", {"text": "cambia el env", "tab": s.tab})            # el lote incluye .env (regla de bloqueo)
    cards = []
    approve_all(s, seen_cards=cards)
    assert cards == []                                                    # comando por regla; .env bloquea el lote
    infos = [e["text"] for e in s.events if e["type"] == "info" and e.get("tab") == s.tab]
    assert any("Aprobado por regla" in t and "npm test" in t for t in infos)
    assert any("Bloqueado por regla" in t and ".env" in t for t in infos)
    assert [e["text"] for e in s.events if e["type"] == "assistant"][-1] == "comando=accept archivos=decline"
    assert (tmp_path / "app.py").read_text().endswith("return 1\n")       # no se aplicó nada del lote


def test_rechazar_y_detener(make_server, tmp_path):
    s = codex_server(make_server, tmp_path, scenario="lento")
    s.api("/api/send", {"text": "algo largo", "tab": s.tab})
    s.wait_for(lambda e: e["type"] == "assistant" and e.get("tab") == s.tab)
    s.api("/api/cmd", {"cmd": "stop", "tab": s.tab})
    s.wait_for(lambda e: e["type"] == "info" and "Interrumpido" in e.get("text", ""))
    for _ in range(40):
        if any(c["method"] == "turn/interrupt" for c in s.calls()):
            break
        time.sleep(0.1)
    assert any(c["method"] == "turn/interrupt" and c["params"]["threadId"] == "th-1" for c in s.calls())


def test_falla_de_codex_se_informa(make_server, tmp_path):
    s = codex_server(make_server, tmp_path, scenario="falla")
    s.api("/api/send", {"text": "hola", "tab": s.tab})
    ev, _ = s.wait_for(lambda e: e["type"] == "error" and e.get("tab") == s.tab, timeout=20)
    assert "Not logged in" in ev["text"]


def test_modo_modelo_y_capturas(make_server, tmp_path):
    s = codex_server(make_server, tmp_path, CODEX_MODELS="default,gpt-5-codex")
    assert s.api("/api/cmd", {"cmd": "model", "arg": "opus", "tab": s.tab})[0] == 400          # modelo de Claude: no
    assert s.api("/api/cmd", {"cmd": "model", "arg": "gpt-5-codex", "tab": s.tab})[0] == 200
    assert s.api("/api/cmd", {"cmd": "mode", "arg": "acceptEdits", "tab": s.tab})[0] == 200
    assert s.api("/api/cmd", {"cmd": "sessions", "tab": s.tab})[0] == 400                       # retomar: solo Claude
    png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    st, r = s.api("/api/send", {"text": "mira", "tab": s.tab, "images": [{"media_type": "image/png", "data": png}]})
    assert st == 400 and "capturas" in r["error"]
    s.api("/api/send", {"text": "hola", "tab": s.tab})
    approve_all(s)
    start = next(c for c in s.calls() if c["method"] == "thread/start")["params"]
    assert start["approvalPolicy"] == "on-request" and start["model"] == "gpt-5-codex"
    turn = next(c for c in s.calls() if c["method"] == "turn/start")["params"]
    assert turn["model"] == "gpt-5-codex" and turn["input"] == [{"type": "text", "text": "hola"}]


def test_sin_codex_instalado_no_se_ofrece(srv):
    st = srv.api("/api/cmd", {"cmd": "status"})[1]["status"]
    assert [e["name"] for e in st["engines"]] == ["claude"]
    st, r = srv.api("/api/cmd", {"cmd": "tab_new", "name": "X", "engine": "codex"})
    assert st == 400 and "no está disponible" in r["error"]


def test_resumen_de_cambios_con_codex(make_server, tmp_path):
    import subprocess
    proy = tmp_path / "proy"
    proy.mkdir()
    (proy / "app.py").write_text("def f():\n    return 1\n")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path)}
    for args in (["init", "-q"], ["add", "."], ["commit", "-qm", "x"]):
        subprocess.run(["git", "-C", str(proy), *args], check=True, capture_output=True, env=env)
    s = codex_server(make_server, tmp_path, cwd=proy)
    s.api("/api/send", {"text": "arregla f", "tab": s.tab})
    approve_all(s)
    ev, _ = s.wait_for(lambda e: e["type"] == "changes" and e.get("tab") == s.tab, timeout=10)
    assert [f["path"] for f in ev["files"]] == ["app.py"] and ev["add"] == 1


@pytest.mark.parametrize("cmd,expected", [
    (["bash", "-lc", "npm test"], "npm test"), (["git", "status", "--short"], "git status --short"),
    ("ls -la", "ls -la"), (["echo", "a b"], "echo 'a b'"),
])
def test_texto_del_comando(cmd, expected):
    from mando.engines.codex import command_text
    assert command_text(cmd) == expected


def test_cambios_en_distintos_formatos():
    from mando.engines.codex import normalize_changes
    lista = normalize_changes([{"path": "a.py", "kind": "add", "diff": "x"}])
    dic = normalize_changes({"b.py": {"type": "update", "unified_diff": "@@"}, "c.py": {"kind": {"type": "update", "move_path": "d.py"}}})
    assert lista == [{"path": "a.py", "kind": "add", "diff": "x", "move_path": None}]
    assert dic[0]["kind"] == "update" and dic[0]["diff"] == "@@" and dic[1]["move_path"] == "d.py"


def test_codex_no_hereda_el_proxy_de_claude(tmp_path):
    code = """
import json
from mando.engines.codex import codex_env
print(json.dumps(codex_env()))
"""
    base = {"CORP_PROXY": "http://claude:1", "HTTPS_PROXY": "http://claude:1", "https_proxy": "http://claude:1",
            "SSL_CERT_FILE": "/ca-claude.pem", "CORP_CA_BUNDLE": "/ca-claude.pem", "NO_PROXY": "local"}
    p = run_python(tmp_path, code, **base)
    assert p.returncode == 0, p.stderr
    env = json.loads(p.stdout.strip().splitlines()[-1])
    assert not {k for k in env if k.upper() in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "SSL_CERT_FILE")}
    p = run_python(tmp_path, code, **base, CODEX_PROXY="http://openai:2", CODEX_CA_BUNDLE="/ca-codex.pem",
                   CODEX_NO_PROXY="interno")
    env = json.loads(p.stdout.strip().splitlines()[-1])
    assert env["HTTPS_PROXY"] == env["HTTP_PROXY"] == "http://openai:2"
    assert env["SSL_CERT_FILE"] == "/ca-codex.pem" and env["NO_PROXY"] == "interno" and "https_proxy" not in env
