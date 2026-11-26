"""Aviso de versión nueva y comprobación de versión al publicar."""

import json
import subprocess
import sys

from conftest import ROOT, run_python

from mando import __version__   # la versión instalada (cambia en cada release)


def test_comparar_versiones(tmp_path):
    code = """
from mando import updates as u
import json
print(json.dumps([u.newer("v0.4.0", "0.3.0"), u.newer("v0.3.0", "0.3.0"), u.newer("v0.2.9", "0.3.0"),
                  u.newer("v.0.10.0", "0.9.9"), u.newer("latest", "0.3.0"), u.parse("v1.2.3")]))
"""
    p = run_python(tmp_path, code)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout.strip().splitlines()[-1]) == [True, False, False, True, False, [1, 2, 3]]


def release(tmp_path, tag):
    f = tmp_path / "release.json"
    f.write_text(json.dumps({"tag_name": tag, "html_url": "https://github.com/x/y/releases/tag/" + tag, "name": tag}))
    return f.as_uri()


def test_aviso_en_el_estado(make_server, tmp_path):
    s = make_server(UPDATE_URL=release(tmp_path, "v9.0.0"), UPDATE_FIRST_DELAY="0")
    s.login()
    ev, _ = s.wait_for(lambda e: e["type"] == "status" and e.get("update"), timeout=10)
    assert ev["update"]["version"] == "v9.0.0" and ev["update"]["url"].endswith("/v9.0.0")
    st = s.api("/api/cmd", {"cmd": "status"})[1]["status"]
    assert st["version"] == __version__ and st["update"]["version"] == "v9.0.0"


def test_sin_aviso_si_esta_al_dia_o_desactivado(make_server, tmp_path):
    s = make_server(UPDATE_URL=release(tmp_path, f"v{__version__}"), UPDATE_FIRST_DELAY="0")
    s.login()
    import time
    time.sleep(1)
    assert s.api("/api/cmd", {"cmd": "status"})[1]["status"]["update"] is None
    s.proc.terminate(); s.proc.wait(5)
    s2 = make_server(UPDATE_URL=release(tmp_path, "v9.0.0"), UPDATE_FIRST_DELAY="0", UPDATE_CHECK="0")
    s2.login()
    time.sleep(1)
    assert s2.api("/api/cmd", {"cmd": "status"})[1]["status"]["update"] is None
    s3 = make_server(UPDATE_URL=(tmp_path / "no-existe.json").as_uri(), UPDATE_FIRST_DELAY="0")   # sin conexión
    s3.login()
    time.sleep(1)
    assert s3.api("/api/cmd", {"cmd": "status"})[1]["status"]["update"] is None


def test_version_en_linea_de_comandos():
    r = subprocess.run([sys.executable, str(ROOT / "server.py"), "--version"], capture_output=True, text=True)
    assert r.stdout.strip() == f"Mando {__version__}"
