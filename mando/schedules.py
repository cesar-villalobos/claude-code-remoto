"""Tareas programadas: una instrucción que se envía sola a una pestaña, a una hora fija.

- Repetida (ciertos días de la semana a una hora) o una sola vez (fecha y hora).
- Usa la hora local del PC donde corre el servidor.
- Se comporta como si la hubieras enviado tú: si Claude está trabajando va a la cola, respeta el
  límite de gasto y **cada permiso sigue pidiendo tu aprobación** (si nadie responde, se rechaza).
- Si el PC estaba apagado a esa hora, no se recupera después (se avisa en el chat).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import re
import secrets
import time
from typing import Any

from .config import log
from .state import STATE, save_state

MAX_SCHEDULES = 20
MAX_NAME = 60
MAX_TEXT = 5000
TICK = float(os.getenv("SCHED_TICK", "20") or 20)   # cada cuántos segundos se revisa
GRACE = 120                                         # una ejecución atrasada hasta 2 min todavía corre
DAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def items() -> list[dict[str, Any]]:
    return STATE.setdefault("schedules", [])


def _local(ts: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(ts)


def next_run(s: dict[str, Any], after: float) -> float | None:
    """Próxima hora (epoch) en que corresponde, estrictamente después de `after`. None si no hay."""
    if not s.get("enabled", True):
        return None
    if s["kind"] == "once":
        when = dt.datetime.fromisoformat(s["at"]).timestamp()
        return when if when > after else None
    hh, mm = map(int, s["time"].split(":"))
    base = _local(after)
    for add in range(0, 8):
        day = (base + dt.timedelta(days=add)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if day.isoweekday() in s["days"] and day.timestamp() > after:
            return day.timestamp()
    return None


def due(s: dict[str, Any], now: float) -> float | None:
    """La hora programada que toca ejecutar ahora (no ejecutada aún), o None."""
    last = s.get("last_run") or s.get("created", now)
    when = next_run(s, last)
    if when is None or when > now:
        return None
    return when


def describe(s: dict[str, Any]) -> str:
    if s["kind"] == "once":
        return "una vez, " + _local(dt.datetime.fromisoformat(s["at"]).timestamp()).strftime("%d-%m-%Y %H:%M")
    days = s["days"]
    when = ("todos los días" if len(days) == 7 else "lun a vie" if days == [1, 2, 3, 4, 5]
            else ", ".join(DAYS[d - 1] for d in days))
    return f"{when} a las {s['time']}"


def view(s: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    nxt = next_run(s, max(now, s.get("last_run") or 0))
    return {**s, "summary": describe(s), "next": nxt}


def validate(body: dict[str, Any], tabs: set[str], now: float | None = None) -> tuple[dict[str, Any] | None, str | None]:
    now = time.time() if now is None else now
    name = " ".join(str(body.get("name") or "").split())[:MAX_NAME]
    text = str(body.get("text") or "").strip()
    tab = str(body.get("target") or "")   # pestaña de destino («tab» es la pestaña desde donde se pide)
    kind = body.get("kind")
    if not name:
        return None, "Ponle un nombre"
    if not text:
        return None, "Escribe la instrucción que se enviará"
    if len(text) > MAX_TEXT:
        return None, f"La instrucción admite hasta {MAX_TEXT} caracteres"
    if tab not in tabs:
        return None, "Esa pestaña ya no existe"
    s: dict[str, Any] = {"name": name, "text": text, "tab": tab, "kind": kind, "enabled": bool(body.get("enabled", True))}
    if kind == "repeat":
        t = str(body.get("time") or "")
        if not TIME_RE.match(t):
            return None, "Hora inválida (usa HH:MM)"
        try:
            days = sorted({int(d) for d in body.get("days") or []})
        except (TypeError, ValueError):
            return None, "Días inválidos"
        if not days or any(d < 1 or d > 7 for d in days):
            return None, "Elige al menos un día"
        s.update(time=t, days=days)
    elif kind == "once":
        try:
            at = dt.datetime.fromisoformat(str(body.get("at") or "")).replace(second=0, microsecond=0)
        except ValueError:
            return None, "Fecha u hora inválida"
        if at.tzinfo is not None:
            return None, "Fecha u hora inválida"
        if at.timestamp() < now - 60:
            return None, "Esa fecha ya pasó"
        s["at"] = at.isoformat(timespec="minutes")
    else:
        return None, "Tipo inválido"
    return s, None


def save(body: dict[str, Any], tabs: set[str]) -> tuple[dict[str, Any] | None, str | None]:
    s, err = validate(body, tabs)
    if err:
        return None, err
    sid = str(body.get("id") or "")
    lst = items()
    old = next((x for x in lst if x["id"] == sid), None) if sid else None
    if old is None and len(lst) >= MAX_SCHEDULES:
        return None, f"Máximo {MAX_SCHEDULES} tareas programadas"
    now = time.time()
    if old:
        old.clear()
        old.update(id=sid, created=now, last_run=None, **s)   # al editar, cuenta desde ahora
        item = old
    else:
        item = {"id": secrets.token_hex(4), "created": now, "last_run": None, **s}
        lst.append(item)
    if item["kind"] == "once":   # «una vez» a esta misma hora: que pueda correr ahora
        item["created"] = min(now, dt.datetime.fromisoformat(item["at"]).timestamp() - 1)
    save_state(STATE)
    return item, None


def delete(sid: str) -> dict[str, Any] | None:
    lst = items()
    s = next((x for x in lst if x["id"] == sid), None)
    if s:
        lst.remove(s)
        save_state(STATE)
    return s


def set_enabled(sid: str, enabled: bool) -> dict[str, Any] | None:
    s = next((x for x in items() if x["id"] == sid), None)
    if s:
        s["enabled"] = enabled
        if enabled:
            s["last_run"] = time.time()   # al reactivar no se ejecuta lo que «se perdió» mientras estaba pausada
        save_state(STATE)
    return s


def mark_run(s: dict[str, Any], when: float) -> None:
    s["last_run"] = when
    if s["kind"] == "once":
        s["enabled"] = False   # ya se usó
    save_state(STATE)


async def tick(run: Any, now: float | None = None) -> int:
    """Una revisión: `run(schedule, missed)` envía la instrucción (missed=True: se perdió). Devuelve cuántas tocaban."""
    now = time.time() if now is None else now
    n = 0
    for s in list(items()):
        when = due(s, now)
        if when is None:
            continue
        missed = now - when > GRACE   # el PC estaba apagado o dormido a esa hora
        mark_run(s, now if missed else when)
        n += 1
        await run(s, missed)
    return n


async def loop(run: Any) -> None:
    while True:
        try:
            await tick(run)
        except Exception:  # noqa: BLE001 — una tarea mal configurada no detiene las demás
            log.exception("Error en las tareas programadas")
        await asyncio.sleep(TICK)
