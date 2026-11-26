"""Exportar una conversación a Markdown."""

from mando.export_md import slug, to_markdown

T0 = 1_763_900_000.0


def test_formato_markdown():
    evs = [
        {"type": "user", "text": "Revisa el login", "images": [{"thumb": "x"}, {"thumb": "y"}], "ts": T0},
        {"type": "stream", "text": "parcial", "ts": T0},                          # no se exporta
        {"type": "tool", "text": "Bash: grep -r `token` src", "ts": T0 + 1},
        {"type": "perm", "ref": "p1", "tool": "Bash", "title": "💻 Ejecutar comando", "detail": "rm -rf build", "ts": T0 + 2},
        {"type": "decided", "ref": "p1", "label": "✅ Aprobado", "ts": T0 + 3},
        {"type": "question", "ref": "q1", "header": "Rama", "question": "¿Qué rama?", "ts": T0 + 4},
        {"type": "decided", "ref": "q1", "label": "→ dev", "ts": T0 + 5},
        {"type": "assistant", "text": "Listo:\n\n```py\nprint(1)\n```", "ts": T0 + 6},
        {"type": "done", "ok": True, "duration": 75, "cost": 0.0123, "tokens": 15200, "ts": T0 + 7},
        {"type": "queued", "qid": "a", "text": "otra", "ts": T0 + 8},                 # no se exporta
        {"type": "changes", "total": 2, "add": 3, "del": 1, "git": True, "ts": T0 + 9, "files": [
            {"path": "src/app.py", "status": "modified", "add": 3, "del": 1, "diff": {}},
            {"path": "logo.png", "status": "added", "binary": True, "add": 0, "del": 0}]},
    ]
    md = to_markdown(evs, name="Principal", cwd="/home/yo/proy", now=T0 + 10)
    assert md.startswith("# Conversación «Principal»\n")
    assert "carpeta `/home/yo/proy`" in md
    assert "## 🧑 Tú ·" in md and "Revisa el login" in md and "_(2 capturas adjuntas, no incluidas)_" in md
    assert "- 🔧 ``Bash: grep -r `token` src``" in md                            # comillas invertidas escapadas
    assert "> **🛂 💻 Ejecutar comando** — ✅ Aprobado" in md and "> rm -rf build" in md
    assert "> **❓ ¿Qué rama?**" in md and "> Respuesta: → dev" in md
    assert "```py\nprint(1)\n```" in md
    assert "_✅ Listo · 1 min 15 s · US$0.0123 · 15.200 tokens_" in md
    assert "parcial" not in md and "otra" not in md
    assert "**📝 Cambios de esta tarea:** 2 archivos · +3 −1" in md
    assert "- ✏️ `src/app.py` (+3 −1)" in md and "- 🆕 `logo.png` (binario)" in md
    assert md.index("Revisa") < md.index("🔧") < md.index("🛂") < md.index("❓") < md.index("print(1)")

    lean = to_markdown(evs, name="Principal", cwd="/x", tools=False, decisions=False)
    assert "🔧" not in lean and "📝" not in lean and "🛂" not in lean and "❓" not in lean and "print(1)" in lean


def test_vacia_y_nombre_de_archivo():
    assert "_La conversación está vacía._" in to_markdown([], name="X", cwd="/")
    assert slug("Front / ABC-123 ñandú") == "front-abc-123-ñandú"
    assert slug("***") == "conversacion"


def test_exportar_desde_la_api(srv):
    srv.api("/api/send", {"text": "revisa ABC-123"})
    perm, _ = srv.wait_for(lambda e: e["type"] == "perm")
    srv.api("/api/decide", {"ref": perm["ref"], "choice": "y"})
    q, _ = srv.wait_for(lambda e: e["type"] == "question")
    srv.api("/api/decide", {"ref": q["ref"], "answer": [1]})
    srv.wait_for(lambda e: e["type"] == "done")
    st, r = srv.api("/api/cmd", {"cmd": "export_md"})
    assert st == 200 and r["filename"].startswith("claude-principal-") and r["filename"].endswith(".md")
    md = r["markdown"]
    assert "revisa ABC-123" in md and "✅ Aprobado" in md and "→ dev" in md and "✅ Listo" in md
    # solo la pestaña pedida
    _, t = srv.api("/api/cmd", {"cmd": "tab_new", "name": "Otra"})
    _, r2 = srv.api("/api/cmd", {"cmd": "export_md", "tab": t["tab"]})
    assert "revisa ABC-123" not in r2["markdown"] and "«Otra»" in r2["markdown"]
    _, body = srv.api("/api/cmd", {"cmd": "audit", "q": "markdown"})
    assert body["events"][0]["event"] == "export_md" and "ABC-123" not in str(body["events"][0])
