"""La respuesta no se repite al terminar, aunque el mensaje definitivo llegue antes del fin del bloque."""

from conftest import run_python


def test_sin_respuesta_repetida_si_el_final_llega_antes(make_server):
    s = make_server("stream_temprano")
    s.login()
    s.api("/api/send", {"text": "arregla el login"})
    _, seen = s.wait_for(lambda e: e["type"] == "done")
    finals = [e for e in seen if e["type"] == "assistant"]
    assert [e["text"] for e in finals] == ["Listo: corregí el login."], finals
    assert finals[0].get("replaces")                           # reemplaza la burbuja en vivo


def test_al_cerrar_no_duplica_lo_ya_respondido(tmp_path):
    """Si una burbuja quedó abierta con el mismo texto que una respuesta definitiva, solo se quita."""
    r = run_python(tmp_path, """
import json
from claude_remoto.agent import AGENTS
from claude_remoto.hub import HUB
sent = []
orig = HUB.emit
HUB.emit = lambda t, **d: sent.append([t, d.get("sid") or d.get("replaces"), d.get("text")]) or orig(t, **d)
a = AGENTS.first()
a.final_texts = {"Hola, listo."}
a.finished_streams = [{"sid": "s1", "text": "Hola, listo.", "msg": "m1"}]
a.stream = {"sid": "s2", "text": "Texto distinto", "msg": "m2"}
a.close_streams()
print(json.dumps(sent))
print(json.dumps([e["type"] for e in HUB.history if e["type"] == "stream_drop"]), json.dumps(sorted(a.final_texts)))
""")
    assert r.returncode == 0, r.stderr
    out = r.stdout.splitlines()
    assert out[0] == '[["stream_drop", "s1", null], ["assistant", "s2", "Texto distinto"]]'
    assert out[1] == "[] []"                                   # stream_drop es efímero; se limpia para la próxima tarea
