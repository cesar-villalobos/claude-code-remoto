"""Cambio de nombre «Claude Remoto» → «Mando»: migración de datos, respaldos antiguos y arranque automático anterior."""

import json
import subprocess
import sys

import pytest

from conftest import ROOT, clean_env

OLD = {".claude-remoto.json": '{"marca": "abc", "rules": []}', ".claude-remoto-history.json": "[]",
       ".claude-remoto-audit.jsonl": '{"event": "login"}\n', ".claude-remoto-usage.jsonl": '{"ts": 1, "cost": 0.5}\n',
       ".claude-remoto.log": "registro\n"}


def py(tmp_path, code, **env):
    e = clean_env(tmp_path, **env)
    if "STATE_FILE" not in env:
        e.pop("STATE_FILE")   # que use la ruta por defecto, como en tu PC
    r = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(ROOT)!r})\n" + code],
                       env=e, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_migra_los_datos_al_nombre_nuevo(tmp_path):
    for name, content in OLD.items():
        (tmp_path / name).write_text(content)
        (tmp_path / name).chmod(0o600)
    out = py(tmp_path, "from mando import migrate, state; print(sorted(migrate.MIGRATED)); print(state.STATE['marca'])")
    moved, token = out.splitlines()
    assert json.loads(moved.replace("'", '"')) == sorted([".mando.json", ".mando-history.json", ".mando-audit.jsonl",
                                                          ".mando-usage.jsonl", ".mando.log"])
    assert token == "abc"                                                   # se conserva el estado (token, sesiones…)
    for name, content in OLD.items():
        new = tmp_path / name.replace(".claude-remoto", ".mando")
        assert not (tmp_path / name).exists() and new.read_text() == content
        assert oct(new.stat().st_mode)[-3:] == "600"                       # mantiene los permisos
    assert py(tmp_path, "from mando import migrate; print(migrate.MIGRATED)") == "[]"   # solo una vez


def test_no_pisa_ni_ignora_rutas_elegidas(tmp_path):
    (tmp_path / ".claude-remoto.json").write_text('{"token": "viejo"}')
    (tmp_path / ".mando.json").write_text('{"token": "nuevo"}')            # ya existe: no se pisa
    (tmp_path / ".claude-remoto-usage.jsonl").write_text("x\n")
    out = py(tmp_path, "from mando import migrate; print(migrate.MIGRATED)", USAGE_FILE=str(tmp_path / "otro.jsonl"))
    assert out == "[]"
    assert (tmp_path / ".claude-remoto.json").exists() and (tmp_path / ".claude-remoto-usage.jsonl").exists()
    assert json.loads((tmp_path / ".mando.json").read_text())["token"] == "nuevo"


@pytest.mark.parametrize("os_name,legacy,cmd", [
    ("Linux", ".config/systemd/user/claude-remoto.service", "systemctl --user disable --now claude-remoto.service"),
    ("Darwin", "Library/LaunchAgents/com.claude-remoto.server.plist", "launchctl bootout gui/"),
    ("Windows", ".claude-remoto-task.xml", "schtasks /Delete /TN ClaudeRemoto /F"),
])
def test_reemplaza_el_arranque_anterior(tmp_path, os_name, legacy, cmd):
    f = tmp_path / legacy
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("antiguo")
    e = clean_env(tmp_path, CR_AUTOSTART_DRYRUN="1", CR_ENV_FILE=str(tmp_path / ".env"), CR_AUTOSTART_OS=os_name,
                  USERNAME="cesar", USERDOMAIN="OFICINA")
    run = lambda *a: subprocess.run([sys.executable, str(ROOT / "server.py"), *a], cwd=tmp_path, env=e,  # noqa: E731
                                    capture_output=True, text=True, timeout=30)
    st = run("--estado-inicio")
    assert "Claude Remoto" in st.stdout and "--instalar-inicio" in st.stdout
    r = run("--instalar-inicio")
    assert r.returncode == 0 and cmd in r.stdout and "Se quitó el arranque automático anterior" in r.stdout
    assert not f.exists()
    assert "Claude Remoto" not in run("--estado-inicio").stdout


def test_importa_respaldos_del_nombre_anterior(srv):
    b = srv.api("/api/cmd", {"cmd": "backup_export"})[1]["backup"]
    assert b["app"] == "mando"
    b["app"] = "claude-remoto"
    st, r = srv.api("/api/backup", {"data": b, "sections": ["templates"], "mode": "merge"})
    assert st == 200, r


def test_env_con_las_rutas_antiguas_conserva_las_pestanas(tmp_path):
    """El caso real: el .env (copiado del .env.example de Claude Remoto) fija las rutas con el nombre antiguo."""
    state = {"marca": "abc", "tabs": [{"id": "t1", "name": "Principal", "cwd": str(tmp_path)},
                                      {"id": "t2", "name": "Front", "cwd": str(tmp_path)}]}
    (tmp_path / ".claude-remoto.json").write_text(json.dumps(state))
    (tmp_path / ".claude-remoto-history.json").write_text("[]")
    out = py(tmp_path, """
from mando import config, migrate
from mando.agent import AGENTS
print([a.name for a in AGENTS]); print(config.STATE_FILE.name); print(migrate.WARNINGS)
""", STATE_FILE=str(tmp_path / ".claude-remoto.json"), HISTORY_FILE="~/.claude-remoto-history.json")
    names, path, warnings = out.splitlines()
    assert names == "['Principal', 'Front']" and path == ".mando.json"      # las pestañas siguen ahí
    assert "cambia STATE_FILE=~/.claude-remoto.json por STATE_FILE=~/.mando.json" in warnings
    assert "HISTORY_FILE" in warnings
    assert not (tmp_path / ".claude-remoto.json").exists() and (tmp_path / ".mando-history.json").exists()
    # en el siguiente arranque, con el .env todavía antiguo, sigue usando el nombre nuevo
    out = py(tmp_path, "from mando.agent import AGENTS; print([a.name for a in AGENTS])",
             STATE_FILE=str(tmp_path / ".claude-remoto.json"))
    assert out == "['Principal', 'Front']"


def test_si_existen_los_dos_avisa_y_no_toca(tmp_path):
    (tmp_path / ".claude-remoto.json").write_text('{"marca": "vacio"}')
    (tmp_path / ".mando.json").write_text('{"marca": "mis-datos"}')
    out = py(tmp_path, "from mando import migrate, state; print(state.STATE['marca']); print(migrate.WARNINGS)",
             STATE_FILE="~/.claude-remoto.json")
    marca, warnings = out.splitlines()
    assert marca == "vacio" and "Existen .claude-remoto.json y .mando.json" in warnings   # no adivina: avisa
    assert (tmp_path / ".claude-remoto.json").exists() and (tmp_path / ".mando.json").exists()


def test_ruta_propia_en_env_se_respeta(tmp_path):
    (tmp_path / "datos").mkdir()
    (tmp_path / "datos" / "estado.json").write_text('{"marca": "propia"}')
    (tmp_path / ".claude-remoto.json").write_text('{"marca": "vieja"}')
    out = py(tmp_path, "from mando import migrate, state; print(state.STATE['marca']); print(migrate.MIGRATED)",
             STATE_FILE=str(tmp_path / "datos" / "estado.json"))
    assert out.splitlines()[0] == "propia" and ".mando.json" not in out
