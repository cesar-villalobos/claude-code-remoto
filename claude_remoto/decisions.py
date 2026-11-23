"""Decisiones pendientes (permisos, preguntas y planes) y su resolución."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from .audit import audit
from .config import DECISION_TIMEOUT
from .hub import HUB


@dataclass
class Pending:
    future: asyncio.Future
    kind: str  # "perm" | "question"
    tool_name: str = ""
    options: list[str] = field(default_factory=list)
    multi: bool = False
    summary: str = ""  # qué se decide (para la auditoría)
    tab: str | None = None  # sesión que la pidió


PENDING: dict[str, Pending] = {}


CANCELLED = "__cancelled__"


def fence_json(data: dict[str, Any], limit: int = 3000) -> str:
    s = json.dumps(data, ensure_ascii=False, indent=2)
    return s if len(s) <= limit else s[:limit] + "\n… (truncado)"


def tool_view(tool_name: str, data: dict[str, Any]) -> tuple[str, str]:
    """(título, detalle) de una petición de herramienta."""
    if tool_name == "Bash":
        return "💻 Ejecutar comando", data.get("command", "")
    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = data.get("file_path") or data.get("notebook_path", "")
        body = data.get("content") or data.get("new_string") or ""
        return f"✏️ {tool_name} · {path}", body[:3000]
    if tool_name.startswith("mcp__") and tool_name.count("__") >= 2:   # mcp__<servidor>__<herramienta>
        _, server, tool = tool_name.split("__", 2)
        return f"🧩 {server} · {tool}", fence_json(data)
    return f"🔧 {tool_name}", fence_json(data)


def tool_trace(tool_name: str, data: dict[str, Any]) -> str:
    target = data.get("file_path") or data.get("pattern") or data.get("path") or data.get("command") or ""
    return f"{tool_name} {str(target).replace(chr(10), ' ')[:140]}".strip()


async def wait_decision(pid: str) -> Any:
    try:
        return await asyncio.wait_for(PENDING[pid].future, timeout=DECISION_TIMEOUT)
    finally:
        PENDING.pop(pid, None)


def resolve(pid: str, value: Any, label: str) -> bool:
    p = PENDING.get(pid)
    if p is None or p.future.done():
        return False
    if value == CANCELLED:
        audit("decision", tool=p.tool_name or "pregunta", target=p.summary, result="cancelada")
    elif p.kind == "perm":
        audit("decision", tool=p.tool_name, target=p.summary, result=label)
    else:
        audit("answer", question=p.summary, answer=str(value)[:500])
    p.future.set_result(value)
    HUB.emit("decided", ref=pid, label=label, **({"tab": p.tab} if p.tab else {}))
    return True

