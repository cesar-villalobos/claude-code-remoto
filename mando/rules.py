"""Reglas de aprobación por patrón (aprobar o bloquear siempre)."""

from __future__ import annotations

import fnmatch
import os
import re
import secrets
from pathlib import Path
from typing import Any

RULE_KINDS = {
    "bash": "Comando",
    "edit": "Edición de archivos",
    "read": "Lectura de archivos",
    "mcp": "Herramienta MCP",
    "web": "Acceso web",
}


EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "ApplyPatch"}   # ApplyPatch: cambios de Codex


READ_TOOLS = {"Read", "Glob", "Grep", "LS"}


# Operadores de shell: un comando que los usa nunca se aprueba automáticamente
SHELL_OPERATORS = re.compile(r"&&|\|\||[;|`\n<>]|\$\(")


MAX_RULE_PATTERN = 200


DEFAULT_RULES = [
    {"action": "deny", "kind": "bash", "pattern": "*rm -rf*", "note": "borrado recursivo"},
    {"action": "deny", "kind": "bash", "pattern": "*rm -fr*", "note": "borrado recursivo"},
    {"action": "deny", "kind": "bash", "pattern": "*git push*--force*", "note": "push forzado"},
    {"action": "deny", "kind": "bash", "pattern": "*git push -f*", "note": "push forzado"},
    {"action": "deny", "kind": "bash", "pattern": "*curl*|*sh*", "note": "ejecutar un script descargado"},
    {"action": "deny", "kind": "bash", "pattern": "*wget*|*sh*", "note": "ejecutar un script descargado"},
    {"action": "deny", "kind": "bash", "pattern": "sudo *", "note": "permisos de administrador"},
    {"action": "deny", "kind": "edit", "pattern": "*.env", "note": "archivos de secretos"},
]


def tool_kind(tool_name: str) -> str | None:
    if tool_name == "Bash":
        return "bash"
    if tool_name in EDIT_TOOLS:
        return "edit"
    if tool_name in READ_TOOLS:
        return "read"
    if tool_name.startswith("mcp__"):
        return "mcp"
    if tool_name in ("WebFetch", "WebSearch"):
        return "web"
    return None


def rule_targets(tool_name: str, data: dict[str, Any], cwd: str) -> list[str]:
    """Textos contra los que se compara el patrón (p. ej. el comando, o la ruta absoluta y relativa)."""
    kind = tool_kind(tool_name)
    if kind == "bash":
        return [str(data.get("command") or "").strip()]
    if kind == "mcp":
        return [tool_name]
    if kind == "web":
        return [str(data.get("url") or data.get("query") or "")]
    raw = str(data.get("file_path") or data.get("notebook_path") or data.get("path") or data.get("pattern") or "")
    if not raw:
        return []
    out = [raw]
    try:
        p = Path(raw).expanduser()
        absolute = p if p.is_absolute() else Path(cwd) / p
        out.append(str(absolute))
        out.append(os.path.relpath(absolute, cwd))
    except ValueError:
        pass
    return list(dict.fromkeys(out))


def rule_matches(rule: dict[str, Any], tool_name: str, data: dict[str, Any], cwd: str) -> bool:
    if tool_kind(tool_name) != rule["kind"]:
        return False
    return any(fnmatch.fnmatchcase(t, rule["pattern"]) for t in rule_targets(tool_name, data, cwd))


def find_rule(rules: list[dict[str, Any]], action: str, tool_name: str, data: dict[str, Any],
              cwd: str) -> dict[str, Any] | None:
    if action == "allow" and tool_kind(tool_name) == "bash" and SHELL_OPERATORS.search(str(data.get("command") or "")):
        return None  # «npm test && rm -rf x» no debe pasar por una regla «npm test*»
    return next((r for r in rules if r["action"] == action and rule_matches(r, tool_name, data, cwd)), None)


def rule_label(rule: dict[str, Any]) -> str:
    return f"{RULE_KINDS.get(rule['kind'], rule['kind'])} «{rule['pattern']}»"


def suggest_rule(tool_name: str, data: dict[str, Any], cwd: str) -> dict[str, str] | None:
    """Sugerencia para «Crear regla» desde una tarjeta."""
    kind = tool_kind(tool_name)
    if kind is None:
        return None
    if kind == "bash":
        words = str(data.get("command") or "").split()
        return {"kind": kind, "pattern": " ".join(words[:2]) + "*" if words else ""}
    targets = rule_targets(tool_name, data, cwd)
    if kind in ("edit", "read") and targets:
        return {"kind": kind, "pattern": targets[-1]}
    return {"kind": kind, "pattern": targets[0] if targets else ""}


def validate_rule(body: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    action, kind = body.get("action"), body.get("kind")
    pattern = str(body.get("pattern") or "").strip()
    note = str(body.get("note") or "").strip()[:100]
    if action not in ("allow", "deny"):
        return None, "Acción inválida"
    if kind not in RULE_KINDS:
        return None, "Tipo inválido"
    if not pattern:
        return None, "El patrón no puede estar vacío"
    if len(pattern) > MAX_RULE_PATTERN:
        return None, f"El patrón supera los {MAX_RULE_PATTERN} caracteres"
    if action == "allow" and pattern.strip("*") == "":
        return None, "Ese patrón aprobaría todo: sé más específico"
    return {"id": secrets.token_hex(4), "action": action, "kind": kind, "pattern": pattern, "note": note}, None

