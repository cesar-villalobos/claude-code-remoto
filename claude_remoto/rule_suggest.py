"""Sugerir reglas: si apruebas varias veces lo mismo, ofrecer «¿Permitir siempre?».

Solo para comandos y herramientas MCP. Nunca sugiere algo riesgoso (borrar, instalar, publicar,
hacer push, cambiar permisos, ejecutar código arbitrario, usar sudo…), ni un patrón que la
validación de reglas rechace o que choque con una regla de bloqueo. Cada sugerencia se puede
aceptar, dejar para después o descartar para siempre.
"""

from __future__ import annotations

import os
import secrets
from typing import Any

from .rules import SHELL_OPERATORS, find_rule, suggest_rule, validate_rule
from .state import STATE, save_state

SUGGEST_AFTER = int(os.getenv("RULE_SUGGEST_AFTER", "2") or 0)   # aprobaciones antes de sugerir (0 = nunca)
MAX_KEYS = 300

# Programas que nunca se sugieren, se usen como se usen
NEVER = {
    "rm", "rmdir", "sudo", "su", "doas", "chmod", "chown", "chgrp", "dd", "mkfs", "fdisk", "diskutil", "kill",
    "killall", "pkill", "shutdown", "reboot", "halt", "curl", "wget", "ssh", "scp", "sftp", "rsync", "nc", "ncat",
    "telnet", "ftp", "eval", "exec", "xargs", "env", "nohup", "crontab", "launchctl", "systemctl", "service",
    "mv", "cp", "ln", "truncate", "shred", "open", "osascript", "powershell", "pwsh", "cmd", "reg", "del", "format",
    "docker", "kubectl", "terraform", "aws", "gcloud", "az", "gh",
}
# Intérpretes: «python script.py» puede sugerirse, «python -c …» no
INTERPRETERS = {"bash", "sh", "zsh", "fish", "python", "python3", "node", "deno", "ruby", "perl", "php", "lua"}
# Subcomandos que cambian cosas fuera de la carpeta o no se pueden deshacer
RISKY_SUB = {
    "git": {"push", "reset", "clean", "rebase", "checkout", "restore", "rm", "branch", "tag", "merge", "switch",
            "stash", "filter-branch", "filter-repo", "remote", "config", "update-ref", "gc", "reflog"},
    "*": {"install", "i", "add", "uninstall", "remove", "publish", "unpublish", "upgrade", "update", "deploy",
          "exec", "dlx", "x", "run-script", "link", "login", "logout", "token", "adduser", "global", "prune"},
}

OFFERS: dict[str, dict[str, Any]] = {}   # sugerencias mostradas y aún sin responder (ref → regla)


def _store() -> dict[str, Any]:
    return STATE.setdefault("rule_suggest", {"counts": {}, "dismissed": []})


def _key(c: dict[str, str]) -> str:
    return f"{c['kind']}:{c['pattern']}"


def candidate(tool_name: str, data: dict[str, Any], cwd: str, rules: list[dict[str, Any]]) -> dict[str, str] | None:
    """La regla que se sugeriría para esta aprobación, o None si no corresponde."""
    s = suggest_rule(tool_name, data, cwd)
    if not s or s["kind"] not in ("bash", "mcp"):
        return None
    if s["kind"] == "bash":
        cmd = str(data.get("command") or "").strip()
        words = cmd.split()
        if not words or SHELL_OPERATORS.search(cmd):
            return None   # con && ; | etc. una regla nunca aplicaría
        prog = os.path.basename(words[0]).lower()
        sub = words[1].lower() if len(words) > 1 else ""
        if prog in NEVER:
            return None
        if prog in INTERPRETERS and (not sub or sub.startswith("-")):
            return None
        if sub in RISKY_SUB.get(prog, set()) or sub in RISKY_SUB["*"]:
            return None
        if any(ch in s["pattern"] for ch in "$~"):   # variables o rutas del usuario: demasiado específico o variable
            return None
    rule, err = validate_rule({"action": "allow", **s, "note": "sugerida"})
    if err:
        return None
    if find_rule(rules, "deny", tool_name, data, cwd):
        return None
    if any(r["action"] == "allow" and r["kind"] == s["kind"] and r["pattern"] == s["pattern"] for r in rules):
        return None
    return {"kind": s["kind"], "pattern": s["pattern"]}


def on_approved(tool_name: str, data: dict[str, Any], cwd: str, rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Cuenta la aprobación; si llegó al umbral, devuelve la sugerencia a mostrar."""
    if SUGGEST_AFTER <= 0:
        return None
    c = candidate(tool_name, data, cwd, rules)
    if not c:
        return None
    st, key = _store(), _key(c)
    if key in st["dismissed"] or any(_key(o) == key for o in OFFERS.values()):
        return None
    counts = st["counts"]
    counts[key] = counts.get(key, 0) + 1
    if len(counts) > MAX_KEYS:   # se olvidan los más antiguos
        for k in list(counts)[: len(counts) - MAX_KEYS]:
            counts.pop(k, None)
    n = counts[key]
    if n < SUGGEST_AFTER:
        save_state(STATE)
        return None
    counts.pop(key, None)   # si se deja para después, se vuelve a sugerir tras otras N aprobaciones
    save_state(STATE)
    ref = secrets.token_hex(5)
    OFFERS[ref] = c
    return {"ref": ref, "count": n, **c}


def take(ref: str) -> dict[str, str] | None:
    return OFFERS.pop(ref, None)


def dismiss_forever(c: dict[str, str]) -> None:
    st = _store()
    key = _key(c)
    if key not in st["dismissed"]:
        st["dismissed"] = (st["dismissed"] + [key])[-MAX_KEYS:]
    st["counts"].pop(key, None)
    save_state(STATE)
