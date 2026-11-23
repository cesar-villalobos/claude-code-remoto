"""Arranque automático (Windows, macOS, Linux), registro en archivo y opciones de línea de comandos."""

import plistlib
import socket
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from conftest import ROOT, TOKEN, clean_env, run_python


def cli(tmp_path, *args, **env):
    e = clean_env(tmp_path, CR_AUTOSTART_DRYRUN="1", CR_ENV_FILE=str(tmp_path / ".env"), **env)
    return subprocess.run([sys.executable, str(ROOT / "server.py"), *args], cwd=tmp_path, env=e,
                          capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("os_name,path,cmds", [
    ("Linux", ".config/systemd/user/claude-remoto.service", ["systemctl --user enable --now claude-remoto.service"]),
    ("Darwin", "Library/LaunchAgents/com.claude-remoto.server.plist", ["launchctl bootstrap gui/"]),
    ("Windows", ".claude-remoto-task.xml", ["schtasks /Create /TN ClaudeRemoto /XML", "schtasks /Run /TN ClaudeRemoto"]),
])
def test_instalar_estado_quitar(tmp_path, os_name, path, cmds):
    r = cli(tmp_path, "--instalar-inicio", CR_AUTOSTART_OS=os_name, USERNAME="cesar", USERDOMAIN="OFICINA")
    assert r.returncode == 0, r.stderr
    for c in cmds:
        assert c in r.stdout
    assert "--mostrar-acceso" in r.stdout
    f = tmp_path / path
    assert f.exists()
    server = str(ROOT / "server.py")

    if os_name == "Linux":
        unit = f.read_text()
        assert f"WorkingDirectory={ROOT}" in unit and "Restart=on-failure" in unit
        assert f"ExecStart={sys.executable} {server} --log-file " in unit
    elif os_name == "Darwin":
        plist = plistlib.loads(f.read_bytes())
        assert plist["ProgramArguments"][:3] == [sys.executable, server, "--log-file"]
        assert plist["WorkingDirectory"] == str(ROOT) and plist["RunAtLoad"] is True
        assert plist["KeepAlive"] == {"SuccessfulExit": False} and "PATH" in plist["EnvironmentVariables"]
    else:
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        root = ET.fromstring(f.read_text(encoding="utf-16").encode("utf-16"))
        assert root.find(".//t:LogonTrigger/t:UserId", ns).text == "OFICINA\\cesar"   # solo este usuario
        assert root.find(".//t:RunLevel", ns).text == "LeastPrivilege"            # sin administrador
        assert root.find(".//t:WorkingDirectory", ns).text == str(ROOT)
        assert server in root.find(".//t:Arguments", ns).text

    r = cli(tmp_path, "--estado-inicio", CR_AUTOSTART_OS=os_name)
    assert "instalado" in r.stdout and "no instalado" not in r.stdout
    r = cli(tmp_path, "--quitar-inicio", CR_AUTOSTART_OS=os_name)
    assert r.returncode == 0 and not f.exists()
    assert "no instalado" in cli(tmp_path, "--estado-inicio", CR_AUTOSTART_OS=os_name).stdout


def test_avisa_variables_que_no_estan_en_env(tmp_path):
    (tmp_path / ".env").write_text("ANTHROPIC_BASE_URL=https://gw\n# HTTPS_PROXY=comentada\n")
    r = cli(tmp_path, "--instalar-inicio", CR_AUTOSTART_OS="Linux",
            HTTPS_PROXY="http://proxy:8080", ANTHROPIC_BASE_URL="https://gw", NODE_EXTRA_CA_CERTS="/ca.pem")
    assert "no las verá" in r.stdout
    line = r.stdout.split("no las verá:")[1].splitlines()[1]
    assert "HTTPS_PROXY" in line and "NODE_EXTRA_CA_CERTS" in line and "ANTHROPIC_BASE_URL" not in line
    assert "http://proxy:8080" not in r.stdout                      # muestra nombres, nunca valores


def test_mostrar_acceso(tmp_path):
    r = cli(tmp_path, "--mostrar-acceso")
    assert r.returncode == 0 and f"?token={TOKEN}" in r.stdout and f"Token: {TOKEN}" in r.stdout


def test_sin_terminal_el_registro_no_tiene_el_token(make_server):
    s = make_server()
    s.login()
    log = s.log.read_text()
    assert "--mostrar-acceso" in log and TOKEN not in log


def test_registro_en_archivo(tmp_path):
    code = f"""
import logging
logging.disable(logging.NOTSET)   # las pruebas lo silencian por defecto
from claude_remoto import app
from claude_remoto.config import log
app.log_to_file({str(tmp_path / 'logs' / 'cr.log')!r})
log.info("hola desde el servicio")
"""
    p = run_python(tmp_path, code)
    assert p.returncode == 0, p.stderr
    f = tmp_path / "logs" / "cr.log"
    assert oct(f.stat().st_mode)[-3:] == "600" and "hola desde el servicio" in f.read_text()


def test_puerto_ocupado(tmp_path):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        e = clean_env(tmp_path, PORT=str(port))
        r = subprocess.run([sys.executable, str(ROOT / "server.py")], cwd=tmp_path, env=e,
                           capture_output=True, text=True, timeout=30)
    assert r.returncode != 0 and "ya está en uso" in r.stderr and "--estado-inicio" in r.stderr
