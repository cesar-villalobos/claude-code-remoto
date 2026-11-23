"""Exportar la conversación de una pestaña a Markdown (para un ticket, un informe o documentación)."""

from __future__ import annotations

import re
import time
from typing import Any

from .hub import HISTORY_MAX

EXPORT_EVENTS = ("user", "assistant", "tool", "info", "error", "done", "perm", "question", "decided", "changes")


def _clock(ts: float, with_date: bool) -> str:
    return time.strftime("%Y-%m-%d %H:%M" if with_date else "%H:%M", time.localtime(ts))


def _code(text: str) -> str:
    """Texto como código en línea, aunque traiga comillas invertidas."""
    text = " ".join(str(text).split())
    ticks = "`" * (max((len(m) for m in re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def _fence(text: str, lang: str = "") -> str:
    fence = "`" * max(3, max((len(m) for m in re.findall(r"`{3,}", text)), default=0) + 1)
    return f"{fence}{lang}\n{text.rstrip()}\n{fence}"


def _quote(text: str) -> str:
    return "\n".join("> " + line if line else ">" for line in str(text).splitlines())


def _duration(secs: float) -> str:
    secs = round(secs)
    return f"{secs // 60} min {secs % 60} s" if secs >= 60 else f"{secs} s"


def slug(name: str) -> str:
    s = re.sub(r"[^\w-]+", "-", name.lower(), flags=re.UNICODE).strip("-")
    return s[:40] or "conversacion"


def to_markdown(events: list[dict[str, Any]], *, name: str, cwd: str, tools: bool = True,
                decisions: bool = True, now: float | None = None) -> str:
    """Arma el Markdown a partir del historial de una pestaña (en orden)."""
    now = now or time.time()
    evs = [e for e in events if e.get("type") in EXPORT_EVENTS]
    answered = {e["ref"]: e.get("label", "") for e in evs if e.get("type") == "decided"}
    out: list[str] = [f"# Conversación «{name}»", ""]
    meta = [f"Exportada el {_clock(now, True)}", f"carpeta {_code(cwd)}"]
    if evs:
        meta.insert(1, f"desde {_clock(evs[0].get('ts', now), True)}")
    out += ["_" + " · ".join(meta) + "_", ""]
    if len([e for e in events]) >= HISTORY_MAX:
        out += [f"> Incluye solo los últimos {HISTORY_MAX} eventos de la sesión.", ""]
    if not evs:
        out.append("_La conversación está vacía._")
        return "\n".join(out) + "\n"

    day = time.strftime("%Y-%m-%d", time.localtime(evs[0].get("ts", now)))
    pending_tools: list[str] = []

    def flush_tools() -> None:
        if pending_tools:
            out.extend(pending_tools)
            out.append("")
            pending_tools.clear()

    for e in evs:
        t, ts = e["type"], e.get("ts", now)
        this_day = time.strftime("%Y-%m-%d", time.localtime(ts))
        when = _clock(ts, this_day != day)
        day = this_day
        if t == "tool":
            if tools:
                pending_tools.append(f"- 🔧 {_code(e.get('text', ''))}")
            continue
        flush_tools()
        if t == "user":
            out += [f"## 🧑 Tú · {when}", "", str(e.get("text", "")).strip() or "_(sin texto)_"]
            n = len(e.get("images") or [])
            if n:
                out.append(f"\n_({n} captura{'s' if n > 1 else ''} adjunta{'s' if n > 1 else ''}, no incluida{'s' if n > 1 else ''})_")
            out.append("")
        elif t == "assistant":
            out += [f"## 🤖 Claude · {when}", "", str(e.get("text", "")).strip(), ""]
        elif t == "info":
            out += [f"_{' '.join(str(e.get('text', '')).split())}_", ""]
        elif t == "error":
            out += [f"**💥 Error:** {_code(e.get('text', ''))}", ""]
        elif t == "done":
            parts = ["✅ Listo" if e.get("ok") else "⚠️ Terminó con error"]
            if e.get("duration"):
                parts.append(_duration(e["duration"]))
            if e.get("cost"):
                parts.append(f"US${e['cost']:.4f}")
            if e.get("tokens"):
                parts.append(f"{e['tokens']:,} tokens".replace(",", "."))
            out += ["_" + " · ".join(parts) + "_", "", "---", ""]
        elif t == "perm" and decisions:
            label = answered.get(e.get("ref"), "⏳ Sin decidir")
            title = "📋 Plan propuesto" if e.get("tool") == "ExitPlanMode" else e.get("title", "Permiso")
            out += [f"> **🛂 {' '.join(str(title).split())}** — {label}"]
            if e.get("tool") == "ExitPlanMode" and e.get("plan"):
                out += [">", _quote(e["plan"])]
            elif e.get("detail") and tools:
                out += [">", _quote(_fence(str(e["detail"])[:4000]))]
            out.append("")
        elif t == "changes" and tools:
            icon = {"added": "🆕", "modified": "✏️", "deleted": "🗑️"}
            n = e.get("total", 0)
            out += [f"**📝 Cambios de esta tarea:** {n} archivo{'s' if n != 1 else ''} · +{e.get('add', 0)} −{e.get('del', 0)}", ""]
            for f in e.get("files") or []:
                counts = "binario" if f.get("binary") else f"+{f.get('add', 0)} −{f.get('del', 0)}"
                out.append(f"- {icon.get(f.get('status'), '•')} {_code(f.get('path', ''))} ({counts})")
            if n > len(e.get("files") or []):
                out.append(f"- … y {n - len(e.get('files') or [])} más")
            out.append("")
        elif t == "question" and decisions:
            label = answered.get(e.get("ref"), "⏳ Sin responder")
            out += [f"> **❓ {' '.join(str(e.get('question') or e.get('header', '')).split())}**", ">",
                    f"> Respuesta: {label}", ""]
    flush_tools()
    while out and out[-1] in ("", "---"):
        out.pop()
    return "\n".join(out) + "\n"
