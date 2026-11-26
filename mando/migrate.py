"""Migración desde «Claude Remoto»: los archivos de datos cambiaron de nombre (~/.claude-remoto* → ~/.mando*).

Se ejecuta una vez, después de leer el .env y antes de abrir cualquier archivo de datos:
- Mueve cada archivo si el nuevo todavía no existe (conserva los permisos 600).
- Si la ruta está fijada en .env con el nombre ANTIGUO por defecto (como venía en el .env.example de
  Claude Remoto), se trata igual que sin fijar: se migra y se usa el nombre nuevo, y se avisa para
  actualizar esa línea. Si apunta a otra ruta elegida por ti, se respeta tal cual.
- Si existen los dos archivos (antiguo y nuevo), no se toca nada y se avisa: hay que elegir a mano.
Así se conservan el token, los dispositivos, el 2FA, las pestañas, las reglas, el historial, la
auditoría y el registro de uso.
"""

from __future__ import annotations

import os
from pathlib import Path

LEGACY = "claude-remoto"   # nombre anterior del proyecto
# (variable de entorno que fija la ruta, nombre anterior, nombre nuevo)
FILES = [
    ("STATE_FILE", ".claude-remoto.json", ".mando.json"),
    ("HISTORY_FILE", ".claude-remoto-history.json", ".mando-history.json"),
    ("AUDIT_FILE", ".claude-remoto-audit.jsonl", ".mando-audit.jsonl"),
    (None, ".claude-remoto-audit.jsonl.1", ".mando-audit.jsonl.1"),   # auditoría rotada (sigue a AUDIT_FILE)
    ("USAGE_FILE", ".claude-remoto-usage.jsonl", ".mando-usage.jsonl"),
    (None, ".claude-remoto.log", ".mando.log"),                       # registro del arranque automático
]

MIGRATED: list[str] = []   # lo que se movió en este arranque (se informa en el banner)
WARNINGS: list[str] = []   # líneas del .env que conviene actualizar o conflictos que resolver a mano
_done = False


def _same(a: str, b: Path) -> bool:
    try:
        return Path(a).expanduser().resolve() == b.resolve()
    except (OSError, ValueError):
        return False


def run(home: Path | None = None) -> list[str]:
    global _done
    if _done and home is None:
        return []
    _done = True
    home = home or Path.home()
    moved: list[str] = []
    audit_custom = False
    for env, old, new in FILES:
        src, dst = home / old, home / new
        value = os.getenv(env) if env else None
        if env == "AUDIT_FILE":
            audit_custom = bool(value) and not _same(value, src)
        if env is None and old.startswith(".claude-remoto-audit") and audit_custom:
            continue   # auditoría en otra ruta elegida por ti: la rotada también se queda donde está
        legacy_env = bool(value) and _same(value, src)
        if value and not legacy_env:
            continue   # ruta elegida por ti: se respeta
        try:
            if src.is_file() and dst.exists():
                if legacy_env or env is None:
                    WARNINGS.append(f"Existen {old} y {new}: se sigue usando {old if legacy_env else new}. "
                                    f"Revisa cuál tiene tus datos y borra el otro.")
                continue
            if src.is_file():
                os.replace(src, dst)   # mismo disco: se mueve sin copiar y conserva los permisos
                moved.append(new)
            if legacy_env:
                os.environ[env] = str(dst)   # en este arranque se usa el nombre nuevo
                WARNINGS.append(f"En tu .env, cambia {env}=~/{old} por {env}=~/{new} (o borra la línea).")
        except OSError:
            pass   # si no se puede, el archivo antiguo queda intacto
    MIGRATED.extend(moved)
    return moved
