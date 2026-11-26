"""Comandos «/» de Claude Code para autocompletar."""

from conftest import run_python


def test_lista_de_comandos(srv):
    st, j = srv.api("/api/cmd", {"cmd": "commands"})
    assert st == 200
    names = [c["name"] for c in j["commands"]]
    # ordenados, sin «/», y se descartan los inválidos (con espacios o sin nombre)
    assert names == ["atlassian:buscar-jira", "compact", "context", "cost", "revisar-pr"]
    compact = next(c for c in j["commands"] if c["name"] == "compact")
    assert compact == {"name": "compact", "description": "Compacta la conversación", "hint": "<instrucciones>"}

    srv.api("/api/cmd", {"cmd": "commands"})                     # la segunda vez usa lo guardado
    log = srv.log.read_text()
    assert log.count("FAKE connect") == 1 and log.count("FAKE get_server_info") == 1


def test_carpeta_nueva_vuelve_a_consultar(srv, tmp_path):
    srv.api("/api/cmd", {"cmd": "commands"})
    (tmp_path / "otro").mkdir()
    assert srv.api("/api/cmd", {"cmd": "cd", "arg": str(tmp_path / "otro")})[0] == 200
    srv.api("/api/cmd", {"cmd": "commands"})
    assert srv.log.read_text().count("FAKE get_server_info") == 2


def test_normalizacion(tmp_path):
    code = """
from mando.agent import normalize_commands
import json
print(json.dumps(normalize_commands(["/clear", {"name": "/x", "argument_hint": "<a>"}, {"name": "x"}, 3, None,
                                     {"name": "a" * 81}, {"name": "B", "description": "d" * 300}])))
"""
    import json
    p = run_python(tmp_path, code)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert [c["name"] for c in out] == ["B", "clear", "x"]
    assert len(out[0]["description"]) == 200
