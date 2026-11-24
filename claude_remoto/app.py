"""Arranque: banner con QR, opciones de línea de comandos y bucle del servidor."""

from __future__ import annotations

import argparse
import asyncio
import errno
import logging
import os
import socket
import ssl
import sys
from pathlib import Path

from . import __version__, daily, push, schedules, updates
from .agent import AGENTS, emit_status
from .api import run_schedule, send_daily_summary
from .auth import is_local
from .config import (
    BIND_HOST,
    CA_CERT,
    PORT,
    TLS_CERT,
    TLS_KEY,
    log,
)
from .hub import HUB
from .state import STATE, save_state
from .web import MAX_BODY, handle


def local_ips() -> list[str]:
    ips: set[str] = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.254.254.254", 1))  # no envía paquetes; solo elige la interfaz
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127.") and is_local(ip))


def print_qr(url: str) -> None:
    try:
        import qrcode  # opcional: pip install qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.print_ascii(invert=True)
    except ImportError:
        print(" (pip install qrcode para ver un QR aquí)")


def print_banner(show_token: bool | None = None) -> None:
    """Direcciones, token y QR. Sin terminal (arranque automático) no se escribe el token en el registro."""
    if show_token is None:
        show_token = bool(sys.stdout and sys.stdout.isatty())
    scheme = "https" if TLS_CERT else "http"
    if not show_token:
        ips = ", ".join(local_ips()) or "<IP-de-este-equipo>"
        log.info("Claude Remoto escuchando en %s://%s:%s (carpeta %s). Para ver el enlace con token y el QR: "
                 "python server.py --mostrar-acceso", scheme, ips, PORT,
                 ", ".join(f"{a.name}: {a.cwd}" for a in AGENTS))
        return
    tok = f"/?token={STATE['token']}"
    private = [f"{scheme}://{ip}:{PORT}{tok}" for ip in local_ips()] or [
        f"{scheme}://<IP-de-este-equipo>:{PORT}{tok}"
    ]
    print("\n" + "=" * 64)
    print(f" Claude Remoto {__version__} (LAN) listo. Abre en el celular (misma red Wi-Fi o por la VPN):")
    for u in private:
        print("   ", u)
    # En el propio equipo, localhost evita que el navegador pase por el proxy corporativo
    print(f"\n 💻 En este equipo: {scheme}://127.0.0.1:{PORT}{tok}")
    if CA_CERT and Path(CA_CERT).is_file():
        ca_base = private[0].split("/?")[0]
        print(f" 📜 CA para instalar en el teléfono: {ca_base}/ca.crt")
    print(f"\n Token: {STATE['token']}")
    print(" Carpeta: " + " · ".join(f"{a.name}: {a.cwd}" for a in AGENTS) if len(AGENTS.items) > 1
          else f" Carpeta: {AGENTS.first().cwd}")
    print_qr(private[0])
    print("=" * 64 + "\n")


async def main() -> None:
    save_state(STATE)
    HUB.load(default_tab=AGENTS.first().id)   # historial de antes de las sesiones paralelas → la primera
    ssl_ctx = None
    if TLS_CERT and TLS_KEY:
        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ssl_ctx.load_cert_chain(os.path.expanduser(TLS_CERT), os.path.expanduser(TLS_KEY))
    try:
        server = await asyncio.start_server(handle, BIND_HOST, PORT, ssl=ssl_ctx, limit=MAX_BODY)
    except OSError as e:
        if e.errno in (errno.EADDRINUSE, 10048):  # 10048: Windows
            raise SystemExit(f"El puerto {PORT} ya está en uso: probablemente Claude Remoto ya está corriendo "
                             "(¿arranque automático?). Mira el estado con: python server.py --estado-inicio\n"
                             "Para ver el enlace y el QR: python server.py --mostrar-acceso") from None
        raise
    print_banner()
    checker = asyncio.create_task(updates.loop(emit_status))   # aviso de versión nueva (UPDATE_CHECK=0 lo apaga)
    planner = asyncio.create_task(schedules.loop(run_schedule))   # tareas programadas
    summarizer = asyncio.create_task(daily.loop(send_daily_summary))   # resumen del día
    if push.PUSH_ENABLED:
        HUB.listeners.append(push.on_event)   # avisos con la app cerrada
    elif not push.AVAILABLE:
        log.info("Web Push desactivado: %s", push.DISABLED_REASON)
    async with server:
        try:
            await server.serve_forever()
        finally:
            checker.cancel()
            planner.cancel()
            summarizer.cancel()
            for agent in AGENTS:
                await agent.stop()
                await agent.close()
            HUB.save()



def disable_2fa() -> None:
    """Recuperación si pierdes el teléfono: requiere acceso al PC."""
    STATE.pop("totp", None)
    save_state(STATE)
    print("Segundo factor desactivado. Vuelve a iniciar el servidor sin la opción.")


def log_to_file(path: str) -> None:
    """Registro en archivo (arranque automático): rotación por tamaño y permisos 600."""
    from logging.handlers import RotatingFileHandler

    f = Path(path).expanduser()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.touch(exist_ok=True)
    f.chmod(0o600)
    h = RotatingFileHandler(f, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(h)
    if sys.stderr is None:  # pythonw (Windows): sin consola, los errores no se perderían en silencio
        sys.stderr = open(f, "a", encoding="utf-8")  # noqa: SIM115


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="server.py", description="Claude Remoto: control de Claude Code desde el celular.")
    p.add_argument("--version", action="version", version=f"Claude Remoto {__version__}")
    p.add_argument("--mostrar-acceso", action="store_true", help="muestra el enlace con token y el QR, sin iniciar")
    p.add_argument("--instalar-inicio", action="store_true", help="arranque automático al iniciar sesión")
    p.add_argument("--quitar-inicio", action="store_true", help="quita el arranque automático")
    p.add_argument("--estado-inicio", action="store_true", help="estado del arranque automático")
    p.add_argument("--desactivar-2fa", action="store_true", help="quita el segundo factor (si perdiste el teléfono)")
    p.add_argument("--log-file", metavar="ARCHIVO", help="escribe el registro en un archivo (lo usa el arranque automático)")
    return p.parse_args(argv)


def run(argv: list[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.desactivar_2fa:
        disable_2fa()
        return
    if args.mostrar_acceso:
        print_banner(show_token=True)
        return
    if args.instalar_inicio or args.quitar_inicio or args.estado_inicio:
        from . import autostart

        action = autostart.install if args.instalar_inicio else autostart.uninstall if args.quitar_inicio else autostart.status
        raise SystemExit(action())
    if args.log_file:
        log_to_file(args.log_file)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
