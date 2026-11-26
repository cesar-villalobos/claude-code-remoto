"""Resumen de cambios al terminar una tarea: herramientas de edición y, en git, todo lo que cambió."""

import subprocess

import pytest
from conftest import run_python


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(cwd)})


def project(tmp_path, with_git=True):
    w = tmp_path / "proy"
    w.mkdir()
    (w / "app.py").write_text("def f():\n    return 1\n")
    (w / "viejo.txt").write_text("adiós\n")
    (w / "conf.ini").write_text("a=1\n")
    (w / "otro.py").write_text("antes\n")
    if with_git:
        git(w, "init", "-q")
        git(w, "add", ".")
        git(w, "commit", "-qm", "inicio")
        (w / "otro.py").write_text("cambio mío previo\n")   # cambio del usuario, anterior a la tarea
    return w


def run_task(make_server, w, **env):
    s = make_server("cambios", CLAUDE_WORKDIR=str(w), ALLOWED_ROOTS=str(w), **env)
    s.login()
    s.api("/api/send", {"text": "arregla f"})
    while True:
        ev, seen = s.wait_for(lambda e: e["type"] in ("perm", "done"))
        if ev["type"] == "done":
            break
        s.api("/api/decide", {"ref": ev["ref"], "choice": "y"})
    return s


def changes_event(s):
    ev, _ = s.wait_for(lambda e: e["type"] == "changes", timeout=8)
    return ev


def test_resumen_en_repositorio_git(make_server, tmp_path):
    w = project(tmp_path)
    s = run_task(make_server, w)
    ev = changes_event(s)
    files = {f["path"]: f for f in ev["files"]}
    assert set(files) == {"app.py", "conf.ini", "logo.png", "nuevo.py", "viejo.txt"}   # no «otro.py» (era previo)
    assert files["app.py"]["status"] == "modified" and files["app.py"]["add"] == 1 and files["app.py"]["del"] == 1
    assert [l["s"] for l in files["app.py"]["diff"]["lines"] if l["t"] == "add"] == ["    return 2"]
    assert files["nuevo.py"]["status"] == "added" and files["nuevo.py"]["diff"]["new_file"]
    assert files["viejo.txt"]["status"] == "deleted"            # hecho «con un comando»: lo ve git
    assert files["conf.ini"]["status"] == "modified"
    assert files["logo.png"].get("binary") and "diff" not in files["logo.png"]
    assert ev["git"] is True and ev["total"] == 5 and ev["tab"]
    # no toca el repositorio
    st = subprocess.run(["git", "-C", str(w), "status", "--porcelain"], capture_output=True, text=True).stdout
    assert " M otro.py" in st
    _, body = s.api("/api/cmd", {"cmd": "audit", "q": "cambios"})
    assert body["events"][0]["event"] == "changes" and body["events"][0]["files"] == 5


def test_sin_git_solo_las_herramientas(make_server, tmp_path):
    w = project(tmp_path, with_git=False)
    s = run_task(make_server, w)
    ev = changes_event(s)
    assert {f["path"] for f in ev["files"]} == {"app.py", "nuevo.py"} and ev["git"] is False


def test_desactivado(make_server, tmp_path):
    w = project(tmp_path)
    s = run_task(make_server, w, CHANGES_SUMMARY="0")
    with pytest.raises(AssertionError, match="no llegó"):
        s.wait_for(lambda e: e["type"] == "changes", timeout=2)


def test_solo_lo_cambiado_en_la_tarea(tmp_path):
    w = project(tmp_path)   # otro.py ya tiene un cambio previo
    code = f"""
from pathlib import Path
from mando.changes import Tracker
w = Path({str(w)!r})
t = Tracker(str(w))
(w / "otro.py").write_text("cambio mío previo\\nmás de la tarea\\n")
s = t.summary()
f = s["files"][0]
print(s["total"], f["path"], f["add"], f["del"], [l["s"] for l in f["diff"]["lines"] if l["t"] == "add"])
t2 = Tracker(str(w))
print(t2.summary())
"""
    r = run_python(tmp_path, code)
    assert r.returncode == 0, r.stderr
    out = r.stdout.splitlines()
    assert out[0] == "1 otro.py 1 0 ['más de la tarea']"     # solo la línea nueva, no el cambio previo
    assert out[1] == "None"                                     # sin cambios: sin resumen
