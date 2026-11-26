"""Respaldo de la configuración: exportar sin secretos e importar con validación."""

import json

from conftest import TOKEN


def export(s):
    st, r = s.api("/api/cmd", {"cmd": "backup_export"})
    assert st == 200
    return r["backup"]


def test_exportar_sin_secretos(srv):
    srv.api("/api/cmd", {"cmd": "jira_set", "url": "https://acme.atlassian.net", "projects": "ABC"})
    b = export(srv)
    assert b["app"] == "mando" and b["format"] == 1
    assert len(b["templates"]) == 5 and any(r["pattern"] == "sudo *" for r in b["rules"])
    assert b["jira"] == {"url": "https://acme.atlassian.net", "projects": ["ABC"]}
    assert [t["name"] for t in b["tabs"]] == ["Principal"]
    raw = json.dumps(b)
    assert TOKEN not in raw and "session" not in raw and "totp" not in raw and '"id"' not in raw


def test_importar_en_otro_pc(make_server, tmp_path):
    a = make_server()
    a.login()
    (tmp_path / "proy").mkdir()
    a.api("/api/cmd", {"cmd": "rule_add", "action": "allow", "kind": "bash", "pattern": "npm test*"})
    a.api("/api/cmd", {"cmd": "template_save", "name": "Mía", "text": "hola", "send": True})
    a.api("/api/cmd", {"cmd": "jira_set", "url": "https://acme.atlassian.net", "projects": ""})
    a.api("/api/cmd", {"cmd": "tab_new", "name": "Proyecto", "cwd": str(tmp_path / "proy")})
    b = export(a)
    b["tabs"].append({"name": "Lejana", "cwd": "/no/existe", "mode": "plan", "model": "opus"})
    b["tabs"].append({"name": "Rara", "model": "modelo-x"})
    b["rules"].append({"action": "allow", "kind": "bash", "pattern": "*"})          # inválida: aprobaría todo
    a.proc.terminate(); a.proc.wait(5)

    other = tmp_path / "otro"
    other.mkdir()
    (other / "proy").mkdir()
    s = make_server(HOME=str(other), STATE_FILE=str(other / "state.json"), CLAUDE_WORKDIR=str(other),
                    ALLOWED_ROOTS=str(other))
    s.login()
    b["tabs"][1]["cwd"] = str(other / "proy")                                       # misma carpeta en el otro PC
    st, r = s.api("/api/backup", {"data": b, "sections": ["rules", "templates", "jira", "tabs"],
                               "mode": "merge"})
    assert st == 200, r
    assert r["added"] == {"rules": 1, "templates": 1, "jira": 1, "tabs": 3}       # Principal ya existía
    assert any("modelo-x" in x for x in r["skipped"])
    assert any("aprobaría todo" in x for x in r["skipped"])
    assert any("/no/existe" in x for x in r["skipped"])
    tabs = {t["name"]: t for t in r["status"]["tabs"]}
    assert tabs["Proyecto"]["cwd"] == str(other / "proy") and tabs["Lejana"]["mode"] == "plan"
    assert tabs["Lejana"]["model"] == "opus" and tabs["Rara"]["model"] in ("", None, "default")
    assert r["status"]["jira"]["url"] == "https://acme.atlassian.net"
    # repetir no duplica
    st, r = s.api("/api/backup", {"data": b, "sections": ["rules", "templates", "tabs"],
                               "mode": "merge"})
    assert r["added"] == {"rules": 0, "templates": 0, "tabs": 0}
    _, body = s.api("/api/cmd", {"cmd": "audit", "q": "respaldo"})
    assert body["events"][0]["event"] == "backup_import"


def test_reemplazar_y_validar(srv):
    b = export(srv)
    b["templates"] = [{"name": "Única", "text": "x", "send": False}]
    b["rules"] = [{"action": "deny", "kind": "bash", "pattern": "*shutdown*", "note": ""}]
    st, r = srv.api("/api/backup", {"data": b, "sections": ["rules", "templates"], "mode": "replace"})
    assert st == 200
    assert [t["name"] for t in srv.api("/api/cmd", {"cmd": "templates"})[1]["templates"]] == ["Única"]
    assert [x["pattern"] for x in srv.api("/api/cmd", {"cmd": "rules"})[1]["rules"]] == ["*shutdown*"]
    assert srv.api("/api/backup", {"data": {"foo": 1}, "sections": ["rules"]})[0] == 400
    assert srv.api("/api/backup", {"data": {**b, "format": 99}, "sections": ["rules"]})[0] == 400


def test_respaldo_grande(srv):
    b = export(srv)
    b["templates"] = [{"name": f"t{i}", "text": "x" * 1990, "send": False} for i in range(25)]   # > 64 KB
    st, r = srv.api("/api/backup", {"data": b, "sections": ["templates"], "mode": "replace"})
    assert st == 200 and r["added"]["templates"] == 25


def test_ruta_de_importacion_protegida(make_server):
    s = make_server()
    st, _, _ = s.request("POST", "/api/backup", {"data": {}, "sections": []})
    assert st == 401                                                    # sin sesión
    s.login()
    import urllib.error
    try:
        st, _, _ = s.request("POST", "/api/backup", {"data": {"x": "x" * 600_000}, "sections": []})
    except (urllib.error.URLError, ConnectionError):
        st = "cerrada"   # el servidor puede cortar la conexión antes de leer todo el cuerpo
    assert st in (413, "cerrada")                                       # sobre 512 KB
