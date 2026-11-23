"""Aviso de versión nueva: consulta la última release del repo público al arrancar y una vez al día.

Es una consulta anónima a GitHub (no envía datos tuyos). Usa el proxy y la CA corporativa si
están configurados. Se desactiva con UPDATE_CHECK=0.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import ssl
import time
import urllib.request
from typing import Any

from . import __version__
from .config import CORP_CA_BUNDLE, CORP_PROXY, log

UPDATE_CHECK = os.getenv("UPDATE_CHECK", "1") not in ("0", "false", "no")
UPDATE_REPO = os.getenv("UPDATE_REPO", "cesar-villalobos/claude-code-remoto")
UPDATE_URL = os.getenv("UPDATE_URL") or f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"
INTERVAL = 24 * 3600
FIRST_DELAY = float(os.getenv("UPDATE_FIRST_DELAY", "20"))   # no retrasar el arranque

LATEST: dict[str, Any] = {}   # {"version", "url", "name", "checked"}


def parse(v: str) -> tuple[int, ...] | None:
    m = re.fullmatch(r"v?\.?(\d+)\.(\d+)\.(\d+)", (v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def newer(latest: str, current: str = __version__) -> bool:
    a, b = parse(latest), parse(current)
    return bool(a and b and a > b)


def fetch() -> dict[str, Any] | None:
    """Última release, o None si no se pudo consultar."""
    handlers: list[Any] = []
    if CORP_PROXY:
        handlers.append(urllib.request.ProxyHandler({"https": CORP_PROXY, "http": CORP_PROXY}))
    ctx = ssl.create_default_context(cafile=os.path.expanduser(CORP_CA_BUNDLE)) if CORP_CA_BUNDLE else None
    handlers.append(urllib.request.HTTPSHandler(context=ctx))
    req = urllib.request.Request(UPDATE_URL, headers={"Accept": "application/vnd.github+json",
                                                      "User-Agent": f"claude-remoto/{__version__}"})
    try:
        with urllib.request.build_opener(*handlers).open(req, timeout=10) as r:
            data = json.loads(r.read(200_000))
    except Exception as e:  # noqa: BLE001 — sin internet, bloqueado o sin releases: no es un error del usuario
        log.debug("No se pudo consultar la versión nueva: %s", e)
        return None
    tag = str(data.get("tag_name") or "")
    if not parse(tag):
        return None
    url = str(data.get("html_url") or "")
    if not url.startswith("https://") and not url.startswith("file:"):
        url = ""
    return {"version": tag, "url": url, "name": str(data.get("name") or tag)[:120], "checked": time.time()}


def update_info() -> dict[str, Any] | None:
    """Lo que ve la interfaz: solo si hay una versión más nueva que la instalada."""
    if LATEST and newer(LATEST["version"]):
        return {"version": LATEST["version"], "url": LATEST["url"], "name": LATEST["name"]}
    return None


async def loop(on_change) -> None:
    if not UPDATE_CHECK:
        return
    await asyncio.sleep(FIRST_DELAY)
    while True:
        info = await asyncio.to_thread(fetch)
        if info:
            changed = info["version"] != LATEST.get("version")
            LATEST.update(info)
            if changed and newer(info["version"]):
                log.info("🆕 Hay una versión nueva de Claude Remoto: %s (tienes %s) %s",
                         info["version"], __version__, info["url"])
                on_change()
        await asyncio.sleep(INTERVAL)
