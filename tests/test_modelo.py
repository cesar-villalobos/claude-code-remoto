"""Elegir el modelo por pestaña."""


def status(s, tab=None):
    return s.api("/api/cmd", {"cmd": "status", **({"tab": tab} if tab else {})})[1]["status"]


def test_lista_y_valor_por_defecto(srv):
    st = status(srv)
    assert st["model"] == "default"
    assert [m["value"] for m in st["models"]] == ["default", "sonnet", "opus", "haiku"]
    assert st["models"][0]["label"] == "Predeterminado"


def test_cambiar_modelo_antes_y_durante(srv):
    assert srv.api("/api/cmd", {"cmd": "model", "arg": "gpt-4"})[0] == 400        # fuera de la lista
    assert srv.api("/api/cmd", {"cmd": "model", "arg": "opus"})[0] == 200
    assert status(srv)["model"] == "opus"
    srv.api("/api/send", {"text": "x"})
    srv.wait_for(lambda e: e["type"] == "perm")
    assert "model=opus" in srv.log.read_text()                                      # se conecta con ese modelo
    srv.api("/api/cmd", {"cmd": "model", "arg": "haiku"})                            # en caliente
    srv.wait_for(lambda e: e["type"] == "info" and "Haiku" in e["text"])
    assert "FAKE set_model haiku" in srv.log.read_text()
    _, body = srv.api("/api/cmd", {"cmd": "audit", "q": "modelo"})
    assert body["events"][0]["event"] == "model"


def test_por_pestana_y_entre_reinicios(make_server):
    s = make_server()
    s.login()
    b = s.api("/api/cmd", {"cmd": "tab_new", "name": "B"})[1]["tab"]
    s.api("/api/cmd", {"cmd": "model", "arg": "sonnet", "tab": b})
    models = {t["name"]: t["model"] for t in status(s)["tabs"]}
    assert models == {"Principal": "default", "B": "sonnet"}
    s.proc.terminate(); s.proc.wait(5)
    s2 = make_server()
    s2.login()
    assert {t["name"]: t["model"] for t in status(s2)["tabs"]} == models


def test_lista_configurable_y_modelo_base(make_server):
    s = make_server(CLAUDE_MODELS="default,claude-sonnet-5", CLAUDE_MODEL="claude-opus-5")
    s.login()
    assert [m["label"] for m in status(s)["models"]] == ["Predeterminado", "claude-sonnet-5"]
    s.api("/api/send", {"text": "x"})
    s.wait_for(lambda e: e["type"] == "perm")
    assert "model=claude-opus-5" in s.log.read_text()                              # «default» usa CLAUDE_MODEL
