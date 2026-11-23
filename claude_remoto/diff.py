"""Diff de las ediciones (Edit / MultiEdit / Write) para las tarjetas de permiso."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

DIFF_MAX_LINES = 400        # líneas máximas enviadas al navegador


DIFF_MAX_LINE_CHARS = 500   # largo máximo de cada línea


DIFF_CONTEXT = 3            # líneas de contexto alrededor de cada cambio


DIFF_MAX_FILE = 1_000_000   # no se leen archivos más grandes (bytes)


def read_text_file(path: str, cwd: str) -> str | None:
    """Contenido actual del archivo, si existe, es texto UTF-8 y no es enorme."""
    try:
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = Path(cwd) / p
        if not p.is_file() or p.stat().st_size > DIFF_MAX_FILE:
            return None
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def file_exists(path: str, cwd: str) -> bool:
    p = Path(path).expanduser()
    return (p if p.is_absolute() else Path(cwd) / p).exists()


def build_diff(old: str, new: str, *, numbered: bool, new_file: bool = False) -> dict[str, Any]:
    """Diff por líneas con contexto, en un formato simple para la interfaz:
    hunk {t, o, n} · ctx {t, o, n, s} · del {t, o, s} · add {t, n, s}."""
    a, b = old.splitlines(), new.splitlines()
    clip = (lambda x: x if len(x) <= DIFF_MAX_LINE_CHARS else x[:DIFF_MAX_LINE_CHARS] + "…")
    lines: list[dict[str, Any]] = []
    added = removed = 0
    for group in difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(DIFF_CONTEXT):
        lines.append({"t": "hunk", "o": group[0][1] + 1, "n": group[0][3] + 1})
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                lines += [{"t": "ctx", "o": i1 + k + 1, "n": j1 + k + 1, "s": clip(a[i1 + k])} for k in range(i2 - i1)]
                continue
            lines += [{"t": "del", "o": k + 1, "s": clip(a[k])} for k in range(i1, i2)]
            lines += [{"t": "add", "n": k + 1, "s": clip(b[k])} for k in range(j1, j2)]
            removed += i2 - i1
            added += j2 - j1
    return {"lines": lines[:DIFF_MAX_LINES], "add": added, "del": removed, "numbered": numbered,
            "new_file": new_file, "truncated": len(lines) > DIFF_MAX_LINES}


def edit_diff(tool_name: str, data: dict[str, Any], cwd: str) -> dict[str, Any] | None:
    """Diff de lo que la herramienta va a cambiar, leyendo el archivo real cuando se puede."""
    path = data.get("file_path") or ""
    if tool_name == "Write":
        new = data.get("content") or ""
        if not file_exists(path, cwd):
            return build_diff("", new, numbered=True, new_file=True)
        current = read_text_file(path, cwd)
        return build_diff(current, new, numbered=True) if current is not None else None
    if tool_name == "Edit":
        edits = [{"old_string": data.get("old_string") or "", "new_string": data.get("new_string") or "",
                  "replace_all": bool(data.get("replace_all"))}]
    elif tool_name == "MultiEdit":
        edits = [e for e in data.get("edits") or [] if isinstance(e, dict)]
    else:
        return None
    current = read_text_file(path, cwd)
    if current is not None:
        updated = current
        for e in edits:
            old, new = e.get("old_string") or "", e.get("new_string") or ""
            if not old or old not in updated:
                break
            updated = updated.replace(old, new) if e.get("replace_all") else updated.replace(old, new, 1)
        else:
            return build_diff(current, updated, numbered=True)  # con números de línea reales
    # Sin archivo legible: antes/después de los fragmentos, sin números de línea
    olds = "\n".join(e.get("old_string") or "" for e in edits)
    news = "\n".join(e.get("new_string") or "" for e in edits)
    return build_diff(olds, news, numbered=False)

