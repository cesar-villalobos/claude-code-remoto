"""Sesiones guardadas de Claude Code y carpetas permitidas."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import ALLOWED_ROOTS


def project_dir(cwd: str) -> Path:
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd)


def first_prompt(path: Path) -> str:
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                obj = json.loads(line)
                if obj.get("type") != "user":
                    continue
                content = obj.get("message", {}).get("content")
                if isinstance(content, list):
                    content = next(
                        (c.get("text") for c in content if isinstance(c, dict) and c.get("type") == "text"), None
                    )
                if isinstance(content, str) and content.strip():
                    return content.strip().replace("\n", " ")[:90]
    except Exception:  # noqa: BLE001
        pass
    return "(sin texto)"


def list_sessions(cwd: str, limit: int = 12) -> list[dict[str, Any]]:
    d = project_dir(cwd)
    if not d.is_dir():
        return []
    files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    return [{"id": p.stem, "title": first_prompt(p), "mtime": p.stat().st_mtime} for p in files]


def resolve_dir(arg: str, cwd: str) -> str | None:
    target = Path(arg).expanduser()
    if not target.is_absolute():
        target = Path(cwd) / target
    target = target.resolve()
    ok = target.is_dir() and any(target == Path(r) or Path(r) in target.parents for r in ALLOWED_ROOTS)
    return str(target) if ok else None

