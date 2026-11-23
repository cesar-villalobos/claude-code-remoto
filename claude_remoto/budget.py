"""Límite de gasto mensual: aviso al acercarse y pausa de las tareas nuevas al llegar.

Cuenta el costo estimado que registra usage.py desde el día 1 del mes en curso (hora local).
Las tareas en curso nunca se cortan: al llegar al límite solo se dejan de iniciar tareas nuevas,
incluidas las que estaban en cola. Se puede continuar igual durante el mes («override») o
subir el límite. La configuración se guarda en el estado y empieza con SPEND_LIMIT_USD.
"""

from __future__ import annotations

import os
import time
from typing import Any

from . import usage
from .state import STATE, save_state

MAX_LIMIT = 100_000.0


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def month_key(now: float | None = None) -> str:
    return time.strftime("%Y-%m", time.localtime(time.time() if now is None else now))


def settings() -> dict[str, Any]:
    saved = STATE.get("budget") or {}
    limit = saved.get("limit", _env_float("SPEND_LIMIT_USD", 0))
    warn = saved.get("warn", int(_env_float("SPEND_WARN_PCT", 80)))
    return {"limit": max(0.0, min(float(limit), MAX_LIMIT)), "warn": max(50, min(int(warn), 100)),
            "override": saved.get("override", "")}


def info(now: float | None = None) -> dict[str, Any]:
    """Estado para la interfaz y para decidir si se inician tareas."""
    s = settings()
    month = month_key(now)
    spent = usage.month_cost(now)
    pct = round(spent / s["limit"] * 100, 1) if s["limit"] else 0.0
    level = "off" if not s["limit"] else "over" if spent >= s["limit"] else "warn" if pct >= s["warn"] else "ok"
    override = level == "over" and s["override"] == month
    return {"limit": s["limit"], "warn": s["warn"], "spent": round(spent, 4), "pct": pct, "level": level,
            "month": month, "override": override, "blocked": level == "over" and not override}


def blocked() -> bool:
    return info()["blocked"]


def blocked_message() -> str:
    i = info()
    return (f"⛔ Llegaste al límite de gasto mensual (US${i['limit']:.2f}): las tareas nuevas están pausadas. "
            "Puedes subir el límite o continuar este mes en ☰ → 💲 Uso.")


def save(limit: Any, warn: Any) -> tuple[dict[str, Any] | None, str | None]:
    try:
        limit = round(float(limit), 2)
        warn = int(warn)
    except (TypeError, ValueError):
        return None, "Límite inválido"
    if not 0 <= limit <= MAX_LIMIT:
        return None, f"El límite debe estar entre 0 y {MAX_LIMIT:,.0f} USD (0 = sin límite)"
    if not 50 <= warn <= 100:
        return None, "El aviso debe estar entre el 50 % y el 100 %"
    STATE["budget"] = {**(STATE.get("budget") or {}), "limit": limit, "warn": warn}
    save_state(STATE)
    return info(), None


def allow_this_month() -> dict[str, Any]:
    STATE["budget"] = {**settings(), **(STATE.get("budget") or {}), "override": month_key()}
    save_state(STATE)
    return info()


def crossed(before: dict[str, Any], after: dict[str, Any]) -> str | None:
    """Aviso para el chat cuando una tarea cruza el umbral de aviso o el límite."""
    order = {"off": 0, "ok": 0, "warn": 1, "over": 2}
    if order[after["level"]] <= order[before["level"]] or before["month"] != after["month"]:
        return None
    if after["level"] == "warn":
        return (f"💲 Llevas US${after['spent']:.2f} de US${after['limit']:.2f} este mes ({after['pct']:.0f} %). "
                "Se acerca el límite de gasto mensual.")
    if after["override"]:
        return f"💲 Superaste el límite mensual de US${after['limit']:.2f}; sigues porque elegiste continuar este mes."
    return blocked_message()
