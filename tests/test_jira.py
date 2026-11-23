"""Configuración de los enlaces a Jira."""


def jira(s):
    return s.api("/api/cmd", {"cmd": "status"})[1]["status"]["jira"]


def test_sin_configurar_no_hay_enlaces(srv):
    j = jira(srv)
    assert j["url"] == "" and j["projects"] == [] and "UTF" in j["exclude"]


def test_configurar_y_validar(srv):
    st, r = srv.api("/api/cmd", {"cmd": "jira_set", "url": " https://acme.atlassian.net/ ", "projects": "abc, dev2;ABC"})
    assert st == 200 and r["jira"]["url"] == "https://acme.atlassian.net" and r["jira"]["projects"] == ["ABC", "DEV2"]
    assert jira(srv)["url"] == "https://acme.atlassian.net"
    for bad in ({"url": "javascript:alert(1)"}, {"url": "ftp://x"}, {"url": "https://x/?q=1"},
                {"url": "https://user@x"}, {"url": "https://x", "projects": "abc-1"},
                {"url": "https://x", "projects": "1AB"}):
        assert srv.api("/api/cmd", {"cmd": "jira_set", **bad})[0] == 400, bad
    assert srv.api("/api/cmd", {"cmd": "jira_set", "url": "", "projects": ""})[0] == 200   # desactivar
    assert jira(srv)["url"] == ""
    _, body = srv.api("/api/cmd", {"cmd": "audit", "q": "jira"})
    assert body["events"][0]["event"] == "jira_set"


def test_desde_env(make_server):
    s = make_server(JIRA_URL="https://env.atlassian.net/", JIRA_PROJECTS="OPS")
    s.login()
    assert jira(s) == {"url": "https://env.atlassian.net", "projects": ["OPS"], "exclude": jira(s)["exclude"]}
