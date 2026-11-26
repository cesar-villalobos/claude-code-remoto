"""Resumen de cambios al terminar una tarea: qué archivos tocó Claude y su diff.

Dos fuentes que se combinan:
- Las herramientas de edición (Edit, MultiEdit, Write, NotebookEdit): antes de cada una se guarda
  el contenido original del archivo (hook PreToolUse), así funciona también fuera de git.
- Si la carpeta es un repositorio git, todo lo que cambió durante la tarea, también lo que hizo con
  comandos (sed, npm, un formateador…): al empezar se guarda el commit y el contenido de los archivos
  que ya tenían cambios, para mostrar solo lo que cambió en esta tarea y no los cambios previos.

No modifica el repositorio (solo lee con git).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .diff import DIFF_MAX_FILE, build_diff

EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit", "ApplyPatch")
MAX_FILES = 50               # archivos con diff en el resumen (el resto solo se cuenta)
MAX_SNAPSHOT = 5_000_000     # bytes guardados al inicio de los archivos que ya tenían cambios
GIT_TIMEOUT = 10
MISSING = object()           # el archivo no existía


def _git(cwd: str, *args: str) -> bytes | None:
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, timeout=GIT_TIMEOUT,
                           env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"})   # no toma locks
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _read(p: Path) -> bytes | None | object:
    """Contenido del archivo; MISSING si no existe; None si es demasiado grande o no se puede leer."""
    try:
        if not p.exists():
            return MISSING
        if not p.is_file() or p.stat().st_size > DIFF_MAX_FILE:
            return None
        return p.read_bytes()
    except OSError:
        return None


def _text(b: bytes | None | object) -> str | None:
    if b is MISSING:
        return ""
    if not isinstance(b, bytes) or b"\0" in b[:8000]:
        return None
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return None


class Tracker:
    """Se crea al empezar la tarea; `before_tool` antes de cada edición; `summary` al terminar."""

    def __init__(self, cwd: str) -> None:
        self.cwd = cwd
        self.originals: dict[str, bytes | None | object] = {}   # ruta absoluta → contenido antes
        self.root: Path | None = None
        self.head: str | None = None
        self.dirty: dict[str, bytes | None | object] = {}       # git: archivos con cambios previos
        top = _git(cwd, "rev-parse", "--show-toplevel")
        if top:
            self.root = Path(top.decode().strip())
            head = _git(cwd, "rev-parse", "--verify", "-q", "HEAD")
            self.head = head.decode().strip() if head else None
            size = 0
            for rel in self._git_changed():
                b = _read(self.root / rel)
                size += len(b) if isinstance(b, bytes) else 0
                self.dirty[rel] = b if size <= MAX_SNAPSHOT else None

    # -- git ---------------------------------------------------------------
    def _git_changed(self) -> list[str]:
        """Archivos distintos del commit inicial (incluye los nuevos sin seguimiento)."""
        if not self.root:
            return []
        out: list[str] = []
        if self.head:
            d = _git(str(self.root), "diff", "--name-only", "-z", "--no-renames", self.head, "--")
            out += [x for x in (d or b"").decode("utf-8", "replace").split("\0") if x]
        u = _git(str(self.root), "ls-files", "-z", "--others", "--exclude-standard")
        out += [x for x in (u or b"").decode("utf-8", "replace").split("\0") if x]
        if not self.head:   # repositorio sin commits: todo lo seguido cuenta como cambio
            s = _git(str(self.root), "ls-files", "-z")
            out += [x for x in (s or b"").decode("utf-8", "replace").split("\0") if x]
        return list(dict.fromkeys(out))

    def _at_head(self, rel: str) -> bytes | None | object:
        if not self.head:
            return MISSING
        b = _git(str(self.root), "cat-file", "-p", f"{self.head}:{rel}")
        if b is None:
            return MISSING
        return b if len(b) <= DIFF_MAX_FILE else None

    # -- herramientas ------------------------------------------------------
    def before_tool(self, tool_name: str, data: dict[str, Any]) -> None:
        if tool_name not in EDIT_TOOLS:
            return
        raw = data.get("file_path") or data.get("notebook_path") or ""
        if not raw:
            return
        p = Path(raw).expanduser()
        p = p if p.is_absolute() else Path(self.cwd) / p
        key = str(p.resolve(strict=False))
        if key not in self.originals:   # solo la primera vez: el contenido antes de la tarea
            self.originals[key] = _read(p)

    # -- resumen -------------------------------------------------------------
    def _rel(self, abspath: str) -> str:
        p = Path(abspath)
        for base in (self.root, Path(self.cwd)):
            if base:
                try:
                    return str(p.relative_to(base.resolve()))
                except ValueError:
                    pass
        return abspath

    def summary(self) -> dict[str, Any] | None:
        before: dict[str, bytes | None | object] = {}
        if self.root:
            root = self.root.resolve()
            for rel in self._git_changed():
                key = str((root / rel).resolve(strict=False))
                before[key] = self.dirty[rel] if rel in self.dirty else self._at_head(rel)
            for rel, b in self.dirty.items():   # tenía cambios y la tarea los deshizo
                key = str((root / rel).resolve(strict=False))
                before.setdefault(key, b)
        for key, b in self.originals.items():
            before[key] = b   # lo leído justo antes de la edición es lo más exacto
        files: list[dict[str, Any]] = []
        for key in sorted(before, key=self._rel):
            old, new = before[key], _read(Path(key))
            if old == new:
                continue
            status = "added" if old is MISSING else "deleted" if new is MISSING else "modified"
            item: dict[str, Any] = {"path": self._rel(key), "status": status}
            a, b = _text(old), _text(new)
            if a is None or b is None:
                item.update(binary=True, add=0, del_=0)
            else:
                d = build_diff(a, b, numbered=True, new_file=status == "added")
                item.update(add=d["add"], del_=d["del"], diff=d)
            files.append(item)
        if not files:
            return None
        for f in files:
            f["del"] = f.pop("del_")
        return {"files": files[:MAX_FILES], "total": len(files), "add": sum(f["add"] for f in files),
                "del": sum(f["del"] for f in files), "git": bool(self.root)}
