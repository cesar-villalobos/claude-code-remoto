"""Arranque automático al iniciar sesión, sin permisos de administrador.

- Windows: tarea del Programador de tareas (al iniciar sesión, reinicio si falla).
- macOS: LaunchAgent en ~/Library/LaunchAgents (RunAtLoad + KeepAlive).
- Linux: servicio de usuario de systemd en ~/.config/systemd/user.

Usa el mismo Python (o entorno virtual) y la misma carpeta desde donde se instala.
Con CR_AUTOSTART_DRYRUN=1 muestra los comandos en vez de ejecutarlos (para las pruebas).
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from .config import APP_DIR, GATEWAY_VARS

NAME = "Mando"
LABEL = "com.mando.server"           # macOS
UNIT = "mando.service"               # Linux
TASK = "Mando"                        # Windows
LOG_FILE = Path("~/.mando.log").expanduser()
# Nombres de antes del cambio de nombre del proyecto: al instalar o quitar se eliminan también
LEGACY = {"macos": "com.claude-remoto.server", "linux": "claude-remoto.service", "windows": "ClaudeRemoto"}
# Variables que la terminal suele tener y el arranque automático no ve
ENV_HINTS = (*GATEWAY_VARS, "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "NODE_EXTRA_CA_CERTS", "CORP_PROXY",
             "CORP_CA_BUNDLE", "ACCESS_TOKEN")


def system() -> str:
    s = os.getenv("CR_AUTOSTART_OS") or platform.system()   # se puede forzar en las pruebas
    return {"Darwin": "macos", "Windows": "windows"}.get(s, "linux")


def python_exe(windows: bool = False) -> str:
    exe = sys.executable
    if windows:  # pythonw.exe: sin ventana de consola
        w = Path(exe).with_name("pythonw.exe")
        if w.exists():
            return str(w)
    return exe


def command() -> list[str]:
    return [python_exe(system() == "windows"), str(APP_DIR / "server.py"), "--log-file", str(LOG_FILE)]


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    if os.getenv("CR_AUTOSTART_DRYRUN"):
        print("DRYRUN:", " ".join(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


# ---------------------------------------------------------------- archivos de cada sistema
def systemd_unit() -> str:
    cmd = " ".join(f'"{c}"' if " " in c else c for c in command())
    return f"""[Unit]
Description={NAME} (control de Claude Code desde el celular)
After=network-online.target

[Service]
WorkingDirectory={APP_DIR}
ExecStart={cmd}
Environment=PATH={os.environ.get("PATH", "/usr/bin:/bin")}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""


def launchd_plist() -> str:
    args = "".join(f"\n        <string>{escape(c)}</string>" for c in command())
    path = escape(os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>{args}
    </array>
    <key>WorkingDirectory</key><string>{escape(str(APP_DIR))}</string>
    <key>EnvironmentVariables</key>
    <dict><key>PATH</key><string>{path}</string></dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
    <key>ThrottleInterval</key><integer>10</integer>
    <key>StandardErrorPath</key><string>{escape(str(LOG_FILE))}.err</string>
</dict>
</plist>
"""


def windows_user() -> str:
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "usuario"
    domain = os.environ.get("USERDOMAIN")
    return f"{domain}\\{user}" if domain else user


def windows_task_xml() -> str:
    exe, *args = command()
    argline = " ".join(f'"{a}"' for a in args)
    user = escape(windows_user())  # solo tu usuario: un disparador para cualquiera exigiría administrador
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>{NAME}: control de Claude Code desde el celular</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{user}</UserId><Delay>PT30S</Delay></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>{user}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>5</Count></RestartOnFailure>
    <StartWhenAvailable>true</StartWhenAvailable>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(exe)}</Command>
      <Arguments>{escape(argline)}</Arguments>
      <WorkingDirectory>{escape(str(APP_DIR))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def target_file() -> Path:
    home = Path.home()
    return {"linux": home / ".config/systemd/user" / UNIT,
            "macos": home / "Library/LaunchAgents" / f"{LABEL}.plist",
            "windows": home / ".mando-task.xml"}[system()]


# ---------------------------------------------------------------- avisos
def env_warnings() -> list[str]:
    """Variables definidas en la terminal que no están en .env (el arranque automático no las verá)."""
    env_file = Path(os.getenv("CR_ENV_FILE") or APP_DIR / ".env")
    in_file = set()
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            k = line.split("=", 1)[0].strip().removeprefix("export ").strip()
            if k and not k.startswith("#"):
                in_file.add(k)
    return [k for k in ENV_HINTS if os.environ.get(k) and k not in in_file]


def legacy_file() -> Path:
    """Archivo que dejó el arranque automático de «Claude Remoto» (antes del cambio de nombre)."""
    home = Path.home()
    return {"linux": home / ".config/systemd/user" / LEGACY["linux"],
            "macos": home / "Library/LaunchAgents" / f"{LEGACY['macos']}.plist",
            "windows": home / ".claude-remoto-task.xml"}[system()]


def remove_legacy() -> bool:
    """Quita el arranque automático con el nombre anterior, si quedó instalado. Devuelve si había uno."""
    sysname, f = system(), legacy_file()
    if not f.exists():
        return False
    if sysname == "linux":
        run(["systemctl", "--user", "disable", "--now", LEGACY["linux"]], check=False)
        f.unlink(missing_ok=True)
        run(["systemctl", "--user", "daemon-reload"], check=False)
    elif sysname == "macos":
        uid = str(os.getuid()) if hasattr(os, "getuid") else "501"
        run(["launchctl", "bootout", f"gui/{uid}/{LEGACY['macos']}"], check=False)
        f.unlink(missing_ok=True)
    else:
        run(["schtasks", "/End", "/TN", LEGACY["windows"]], check=False)
        run(["schtasks", "/Delete", "/TN", LEGACY["windows"], "/F"], check=False)
        f.unlink(missing_ok=True)
    print("🧹 Se quitó el arranque automático anterior («Claude Remoto»).")
    return True


# ---------------------------------------------------------------- acciones
def install() -> int:
    remove_legacy()   # si venías de «Claude Remoto», no quedan dos servidores compitiendo por el puerto
    sysname, f = system(), target_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    if sysname == "linux":
        if not shutil.which("systemctl") and not os.getenv("CR_AUTOSTART_DRYRUN"):
            print("No se encontró systemd (systemctl). Configura el arranque con tu gestor de sesión.")
            return 1
        f.write_text(systemd_unit())
        run(["systemctl", "--user", "daemon-reload"])
        run(["systemctl", "--user", "enable", "--now", UNIT])
    elif sysname == "macos":
        f.write_text(launchd_plist())
        uid = str(os.getuid()) if hasattr(os, "getuid") else "501"
        run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], check=False)   # si ya estaba
        run(["launchctl", "bootstrap", f"gui/{uid}", str(f)])
    else:
        f.write_text(windows_task_xml(), encoding="utf-16")
        run(["schtasks", "/Create", "/TN", TASK, "/XML", str(f), "/F"])
        run(["schtasks", "/Run", "/TN", TASK], check=False)
    print(f"✅ Arranque automático instalado ({ {'linux': 'systemd de usuario', 'macos': 'LaunchAgent', 'windows': 'Programador de tareas'}[sysname] }).")
    print(f"   Se inicia al iniciar sesión, desde {APP_DIR}, con {python_exe(sysname == 'windows')}.")
    print(f"   Registro: {LOG_FILE}")
    print("   Para ver la dirección y el QR:  python server.py --mostrar-acceso")
    if sysname == "linux":
        print("   Para que arranque aunque no inicies sesión:  loginctl enable-linger $USER")
    missing = env_warnings()
    if missing:
        print("\n⚠️  Estas variables están en tu terminal pero no en .env, y el arranque automático no las verá:")
        print("   " + ", ".join(missing))
        print("   Cópialas a .env (en la carpeta del proyecto) y reinicia el servicio.")
    return 0


def uninstall() -> int:
    remove_legacy()
    sysname, f = system(), target_file()
    if sysname == "linux":
        run(["systemctl", "--user", "disable", "--now", UNIT], check=False)
        f.unlink(missing_ok=True)
        run(["systemctl", "--user", "daemon-reload"], check=False)
    elif sysname == "macos":
        uid = str(os.getuid()) if hasattr(os, "getuid") else "501"
        run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], check=False)
        f.unlink(missing_ok=True)
    else:
        run(["schtasks", "/End", "/TN", TASK], check=False)
        run(["schtasks", "/Delete", "/TN", TASK, "/F"], check=False)
        f.unlink(missing_ok=True)
    print("Arranque automático quitado. El servidor ya no se iniciará solo.")
    return 0


def status() -> int:
    sysname, f = system(), target_file()
    installed = f.exists()
    print(f"Arranque automático: {'instalado' if installed else 'no instalado'} ({f})")
    if legacy_file().exists():
        print("⚠️  Sigue instalado el arranque automático de «Claude Remoto» (nombre anterior). "
              "Para reemplazarlo: python server.py --instalar-inicio")
    if installed:
        cmd = {"linux": ["systemctl", "--user", "is-active", UNIT],
               "macos": ["launchctl", "print", f"gui/{os.getuid() if hasattr(os, 'getuid') else 501}/{LABEL}"],
               "windows": ["schtasks", "/Query", "/TN", TASK]}[sysname]
        r = run(cmd, check=False)
        out = (r.stdout or "").strip()
        if out:
            print("\n".join(out.splitlines()[:6]))
        print(f"Registro: {LOG_FILE}")
    return 0
