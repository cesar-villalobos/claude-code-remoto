"""Cola de mensajes: lo enviado mientras Claude trabaja se ejecuta después, en orden."""


def status(s):
    return s.api("/api/cmd", {"cmd": "status"})[1]["status"]


def answer_task(s, perm=None):
    if perm is None:
        perm, _ = s.wait_for(lambda e: e["type"] == "perm")
    s.api("/api/decide", {"ref": perm["ref"], "choice": "y"})
    q, _ = s.wait_for(lambda e: e["type"] == "question")
    s.api("/api/decide", {"ref": q["ref"], "answer": [0]})
    s.wait_for(lambda e: e["type"] == "done")


def test_se_ejecuta_al_terminar_en_orden(srv):
    srv.api("/api/send", {"text": "primera"})
    first, _ = srv.wait_for(lambda e: e["type"] == "perm")
    assert srv.api("/api/send", {"text": "segunda"})[1]["queued"] is True
    assert srv.api("/api/send", {"text": "tercera"})[1]["queued"] is True
    q2, _ = srv.wait_for(lambda e: e["type"] == "queued" and e["text"] == "segunda")
    q3, _ = srv.wait_for(lambda e: e["type"] == "queued" and e["text"] == "tercera")
    assert status(srv)["queued"] == 2

    answer_task(srv, first)                                     # termina «primera»…
    ev, _ = srv.wait_for(lambda e: e["type"] == "user" and e["text"] == "segunda")
    assert ev["qid"] == q2["qid"]                               # …y empieza sola la siguiente
    assert status(srv)["queued"] == 1 and status(srv)["busy"]
    answer_task(srv)
    ev, _ = srv.wait_for(lambda e: e["type"] == "user" and e["text"] == "tercera")
    assert ev["qid"] == q3["qid"]
    answer_task(srv)
    assert status(srv)["queued"] == 0


def test_quitar_y_detener_vacia_la_cola(srv):
    srv.api("/api/send", {"text": "primera"})
    srv.wait_for(lambda e: e["type"] == "perm")
    srv.api("/api/send", {"text": "a"})
    srv.api("/api/send", {"text": "b"})
    qa, _ = srv.wait_for(lambda e: e["type"] == "queued" and e["text"] == "a")
    qb, _ = srv.wait_for(lambda e: e["type"] == "queued" and e["text"] == "b")
    assert srv.api("/api/cmd", {"cmd": "queue_remove", "arg": qa["qid"]})[0] == 200
    srv.wait_for(lambda e: e["type"] == "unqueued" and e["qid"] == qa["qid"])
    assert srv.api("/api/cmd", {"cmd": "queue_remove", "arg": qa["qid"]})[0] == 404
    srv.api("/api/cmd", {"cmd": "stop"})                         # detener = retomar el control
    ev, _ = srv.wait_for(lambda e: e["type"] == "unqueued" and e["qid"] == qb["qid"])
    assert "detuvo" in ev["label"]
    srv.wait_for(lambda e: e["type"] == "status" and not e["tabs"][0]["busy"])
    assert status(srv)["queued"] == 0
    snap = srv.poll(-1, timeout=0)
    assert not any(e["type"] == "user" and e["text"] in ("a", "b") for e in snap["history"])


def test_limite_y_por_pestana(srv):
    b = srv.api("/api/cmd", {"cmd": "tab_new", "name": "B"})[1]["tab"]
    srv.api("/api/send", {"text": "ocupada"})
    srv.wait_for(lambda e: e["type"] == "perm")
    assert srv.api("/api/send", {"text": "en B", "tab": b})[1]["queued"] is False   # B está libre
    for i in range(10):
        assert srv.api("/api/send", {"text": f"m{i}"})[0] == 200
    st, r = srv.api("/api/send", {"text": "una más"})
    assert st == 409 and "10" in r["error"]


def test_cola_pendiente_tras_reinicio(make_server):
    s = make_server()
    s.login()
    s.api("/api/send", {"text": "primera"})
    s.wait_for(lambda e: e["type"] == "perm")
    s.api("/api/send", {"text": "pendiente"})
    q, _ = s.wait_for(lambda e: e["type"] == "queued")
    import time
    time.sleep(1.2)
    s.proc.terminate(); s.proc.wait(5)
    s2 = make_server()
    s2.login()
    hist = s2.poll(-1, timeout=0)["history"]
    ev = next(e for e in hist if e["type"] == "unqueued" and e["qid"] == q["qid"])
    assert "reinició" in ev["label"]
