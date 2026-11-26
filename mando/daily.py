"""Resumen del día: tareas, costo, archivos cambiados, decisiones y errores por pestaña.

Se puede pedir en cualquier momento (☰ → 📊 Resumen de hoy) y, si se activa, llega solo a una hora
fija (hora del PC) los días en que hubo actividad. Se arma con el historial del día y el registro
de uso (costo y tokens); no incluye textos de las tareas ni de las respuestas.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections import defaultdict
from typing import Any

from . import usage
from .config import log
from .state import STATE, save_state

TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
TICK = float(os.getenv("DAILY_TICK", "30") or 30)
APPROVED = ("✅", "✏️", "⚖️")
REJECTED = ("❌", "💬", "⌛", "⏹️")


def settings() -> dict[str, Any]:
    saved = STATE.get("daily") or {}
    env_at = os.getenv("DAILY_SUMMARY_AT", "").strip()
    at = saved.get("at", env_at if TIME_RE.match(env_at) else "18:00")
    enabled = saved.get("enabled", bool(TIME_RE.match(env_at)))
    return {"enabled": bool(enabled), "at": at, "last": saved.get("last", "")}


def save(enabled: Any, at: Any) -> tuple[dict[str, Any] | None, str | None]:
    at = str(at or "")
    if not TIME_RE.match(at):
        return None, "Hora inválida (usa HH:MM)"
    STATE["daily"] = {**(STATE.get("daily") or {}), "enabled": bool(enabled), "at": at}
    save_state(STATE)
    return settings(), None


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def build(history: list[dict[str, Any]], tab_names: dict[str, str], now: float | None = None) -> dict[str, Any]:
    """Resumen de un día a partir del historial (eventos) y del registro de uso."""
    now = time.time() if now is None else now
    today = _day(now)
    per: dict[str, dict[str, Any]] = defaultdict(lambda: {"tasks": 0, "ok": 0, "failed": 0, "approved": 0,
                                                          "rejected": 0, "files": set(), "add": 0, "del": 0,
                                                          "scheduled": 0, "duration": 0.0})
    for e in history:
        if _day(e.get("ts", 0)) != today:
            continue
        tab, t = e.get("tab"), e.get("type")
        if not tab:
            continue
        p = per[tab]
        if t == "done":
            p["tasks"] += 1
            p["ok" if e.get("ok") else "failed"] += 1
            p["duration"] += e.get("duration") or 0
        elif t == "error":
            p["tasks"] += 1
            p["failed"] += 1
        elif t == "decided":
            label = str(e.get("label") or "")
            if label.startswith(APPROVED):
                p["approved"] += 1
            elif label.startswith(REJECTED):
                p["rejected"] += 1
        elif t == "changes":
            for f in e.get("files") or []:
                p["files"].add(f.get("path"))
            p["add"] += e.get("add") or 0
            p["del"] += e.get("del") or 0
        elif t == "info" and str(e.get("text") or "").startswith("⏰ Tarea programada"):
            p["scheduled"] += 1
    cost = usage.summary(now)
    by_tab_cost = {r["name"]: r for r in cost["tabs"]}   # el registro de uso agrupa por nombre de pestaña
    tabs = []
    for tab, p in per.items():
        name = tab_names.get(tab, "(pestaña cerrada)")
        if not (p["tasks"] or p["approved"] or p["rejected"] or p["files"]):
            continue
        tabs.append({"name": name, "tasks": p["tasks"], "ok": p["ok"], "failed": p["failed"],
                     "approved": p["approved"], "rejected": p["rejected"], "files": len(p["files"]),
                     "add": p["add"], "del": p["del"], "scheduled": p["scheduled"], "duration": round(p["duration"]),
                     "cost": (by_tab_cost.get(name) or {}).get("cost", 0)})
    tabs.sort(key=lambda x: (-x["tasks"], x["name"]))
    t = cost["totals"]["today"]
    return {"day": today, "tabs": tabs, "tasks": sum(x["tasks"] for x in tabs), "failed": sum(x["failed"] for x in tabs),
            "approved": sum(x["approved"] for x in tabs), "rejected": sum(x["rejected"] for x in tabs),
            "files": sum(x["files"] for x in tabs), "add": sum(x["add"] for x in tabs), "del": sum(x["del"] for x in tabs),
            "cost": t["cost"], "tokens": t["in"] + t["out"], "duration": sum(x["duration"] for x in tabs)}


def is_due(now: float | None = None) -> bool:
    s = settings()
    if not s["enabled"]:
        return False
    now = time.time() if now is None else now
    today = _day(now)
    return s["last"] != today and time.strftime("%H:%M", time.localtime(now)) >= s["at"]


def mark_sent(now: float | None = None) -> None:
    STATE["daily"] = {**(STATE.get("daily") or {}), "last": _day(time.time() if now is None else now)}
    save_state(STATE)


async def loop(send: Any) -> None:
    """`send()` arma y publica el resumen; se llama una vez al día, a la hora elegida."""
    while True:
        try:
            if is_due():
                mark_sent()   # aunque falle el envío, no se reintenta en bucle
                await send()
        except Exception:  # noqa: BLE001
            log.exception("Error en el resumen diario")
        await asyncio.sleep(TICK)
