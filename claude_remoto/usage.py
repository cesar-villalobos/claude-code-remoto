"""Costo y uso: una línea por tarea terminada (JSONL local, permisos 600), sin el texto de la tarea.

El costo es la estimación que entrega Claude Code según los precios públicos; con un gateway
corporativo el cobro real puede ser distinto, por eso también se guardan los tokens.
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import log

USAGE_FILE = Path(os.getenv("USAGE_FILE", "~/.claude-remoto-usage.jsonl")).expanduser()
DAYS_CHART = 14


def record(*, tab: str, tab_id: str, cost: float | None, usage: dict[str, Any] | None, duration: float,
           turns: int | None, model: str, ok: bool, now: float | None = None) -> dict[str, Any]:
    u = usage or {}
    rec = {
        "ts": round(now if now is not None else time.time(), 3), "tab": tab, "tab_id": tab_id, "model": model,
        "ok": ok, "cost": round(float(cost or 0), 6), "duration": round(duration, 1), "turns": turns or 0,
        "in": int(u.get("input_tokens") or 0), "out": int(u.get("output_tokens") or 0),
        "cache_read": int(u.get("cache_read_input_tokens") or 0),
        "cache_write": int(u.get("cache_creation_input_tokens") or 0),
    }
    try:
        new = not USAGE_FILE.exists()
        with USAGE_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if new:
            USAGE_FILE.chmod(0o600)
    except OSError:
        log.exception("No se pudo registrar el uso")
    return rec


def _records() -> list[dict[str, Any]]:
    try:
        lines = USAGE_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            r = json.loads(line)
            if isinstance(r, dict) and "ts" in r:
                out.append(r)
        except json.JSONDecodeError:
            continue
    return out


_month_cache: dict[str, Any] = {}


def month_cost(now: float | None = None) -> float:
    """Costo estimado desde el día 1 del mes en curso (hora local). Se recalcula solo si cambió el archivo."""
    now = time.time() if now is None else now
    month = time.strftime("%Y-%m", time.localtime(now))
    try:
        st = USAGE_FILE.stat()
        key = (str(USAGE_FILE), st.st_mtime_ns, st.st_size, month)
    except OSError:
        return 0.0
    if _month_cache.get("key") != key:
        total = sum(r.get("cost") or 0 for r in _records()
                    if time.strftime("%Y-%m", time.localtime(r["ts"])) == month)
        _month_cache.update(key=key, total=total)
    return _month_cache["total"]


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _empty() -> dict[str, Any]:
    return {"tasks": 0, "cost": 0.0, "in": 0, "out": 0, "cache": 0, "duration": 0.0}


def _add(acc: dict[str, Any], r: dict[str, Any]) -> None:
    acc["tasks"] += 1
    acc["cost"] += r.get("cost") or 0
    acc["in"] += r.get("in") or 0
    acc["out"] += r.get("out") or 0
    acc["cache"] += (r.get("cache_read") or 0) + (r.get("cache_write") or 0)
    acc["duration"] += r.get("duration") or 0


def _round(acc: dict[str, Any]) -> dict[str, Any]:
    return {**acc, "cost": round(acc["cost"], 4), "duration": round(acc["duration"])}


def summary(now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    today = _day(now)
    days = [_day(now - 86400 * i) for i in range(DAYS_CHART - 1, -1, -1)]
    since7, since30 = _day(now - 86400 * 6), _day(now - 86400 * 29)
    totals = {"today": _empty(), "week": _empty(), "month": _empty()}
    per_day = {d: _empty() for d in days}
    per_tab: dict[str, dict[str, Any]] = defaultdict(_empty)
    per_model: dict[str, dict[str, Any]] = defaultdict(_empty)
    for r in _records():
        d = _day(r["ts"])
        if d < since30 or d > today:
            continue
        _add(totals["month"], r)
        _add(per_tab[r.get("tab") or "—"], r)
        _add(per_model[r.get("model") or "default"], r)
        if d >= since7:
            _add(totals["week"], r)
        if d == today:
            _add(totals["today"], r)
        if d in per_day:
            _add(per_day[d], r)
    by = lambda m: sorted(({"name": k, **_round(v)} for k, v in m.items()),  # noqa: E731
                          key=lambda x: (-x["cost"], -x["tasks"]))
    return {
        "totals": {k: _round(v) for k, v in totals.items()},
        "days": [{"day": d, **_round(per_day[d])} for d in days],
        "tabs": by(per_tab),
        "models": by(per_model),
        "file": str(USAGE_FILE),
    }
