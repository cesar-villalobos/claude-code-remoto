"""Plantillas: ejemplos iniciales, edición, validación, orden y sincronización."""


def tpl(s):
    return s.api("/api/cmd", {"cmd": "templates"})[1]["templates"]


def version(s):
    return s.api("/api/cmd", {"cmd": "status"})[1]["status"]["tpl"]


def test_ejemplos_iniciales(srv):
    items = tpl(srv)
    assert [t["name"] for t in items][:2] == ["🎫 Revisar ticket", "🧪 Correr pruebas"]
    assert {t["send"] for t in items} == {True, False}
    ticket = items[0]
    assert "{ticket}" in ticket["text"] and ticket["send"] is False


def test_crear_editar_mover_borrar(srv):
    v0 = version(srv)
    st, r = srv.api("/api/cmd", {"cmd": "template_save", "name": "🚀 Deploy", "text": "Prepara el deploy", "send": True})
    assert st == 200 and r["templates"][-1]["name"] == "🚀 Deploy" and r["templates"][-1]["send"] is True
    v1 = version(srv)
    assert v1 != v0                                         # los demás dispositivos recargan la lista
    new = r["templates"][-1]

    # con un {campo} por completar nunca se envía directo
    st, r = srv.api("/api/cmd", {"cmd": "template_save", "id": new["id"], "name": "🚀 Deploy",
                                 "text": "Prepara el deploy a {ambiente}", "send": True})
    assert st == 200 and next(t for t in r["templates"] if t["id"] == new["id"])["send"] is False

    st, r = srv.api("/api/cmd", {"cmd": "template_move", "arg": new["id"], "delta": -1})
    assert [t["id"] for t in r["templates"]].index(new["id"]) == len(r["templates"]) - 2
    assert version(srv) != v1

    st, r = srv.api("/api/cmd", {"cmd": "template_del", "arg": new["id"]})
    assert st == 200 and all(t["id"] != new["id"] for t in r["templates"])
    assert srv.api("/api/cmd", {"cmd": "template_del", "arg": new["id"]})[0] == 404

    _, body = srv.api("/api/cmd", {"cmd": "audit", "q": "plantilla"})
    assert {e["event"] for e in body["events"]} >= {"template_save", "template_del"}


def test_validacion_y_limite(srv):
    bad = [{"name": "", "text": "x"}, {"name": "x", "text": ""}, {"name": "n" * 41, "text": "x"},
           {"name": "x", "text": "t" * 2001}]
    for b in bad:
        assert srv.api("/api/cmd", {"cmd": "template_save", **b})[0] == 400
    assert srv.api("/api/cmd", {"cmd": "template_save", "id": "nope", "name": "x", "text": "y"})[0] == 404
    for i in range(30 - len(tpl(srv))):
        assert srv.api("/api/cmd", {"cmd": "template_save", "name": f"p{i}", "text": "x"})[0] == 200
    st, r = srv.api("/api/cmd", {"cmd": "template_save", "name": "una más", "text": "x"})
    assert st == 400 and "30" in r["error"]
    st, r = srv.api("/api/cmd", {"cmd": "templates_reset"})
    assert st == 200 and len(r["templates"]) == 5


def test_se_guardan_entre_reinicios(make_server):
    s = make_server()
    s.login()
    s.api("/api/cmd", {"cmd": "template_save", "name": "Mía", "text": "hola"})
    s.proc.terminate(); s.proc.wait(5)
    s2 = make_server()
    s2.login()
    assert tpl(s2)[-1]["name"] == "Mía"
