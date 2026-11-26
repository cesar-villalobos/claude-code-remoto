"""Respaldo de la configuración: exportar e importar reglas, plantillas, Jira y pestañas.

Nunca incluye secretos ni datos de uso: ni el token, ni las sesiones de los dispositivos,
ni el segundo factor, ni la auditoría, ni el historial del chat.
"""

from __future__ import annotations

import time
from typing import Any

from . import budget, jira
from . import templates as tpl
from .agent import AGENTS, MAX_TAB_NAME, MAX_TABS, shared_rules
from .claude_sessions import resolve_dir
from .config import MODELS, PERMISSION_MODES
from .rules import validate_rule
from .state import STATE, save_state

FORMAT = 1
SECTIONS = ("rules", "templates", "jira", "budget", "tabs")
MAX_BACKUP = 512 * 1024   # bytes del cuerpo de la petición de importación


def export_data() -> dict[str, Any]:
    return {
        "app": "mando", "format": FORMAT, "exported": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "rules": [{k: r.get(k, "") for k in ("action", "kind", "pattern", "note")} for r in shared_rules()],
        "templates": [{k: t[k] for k in ("name", "text", "send")} for t in tpl.templates()],
        "jira": {k: v for k, v in jira.config().items() if k in ("url", "projects")},
        "budget": {k: v for k, v in budget.settings().items() if k in ("limit", "warn")},   # sin «continuar este mes»
        "tabs": [{"name": a.name, "cwd": a.cwd, "mode": a.mode, "model": a.model} for a in AGENTS],
    }


def preview(data: Any) -> tuple[dict[str, int] | None, str | None]:
    """Cuántos elementos trae cada sección (para confirmar antes de importar)."""
    if not isinstance(data, dict) or data.get("app") not in ("mando", "claude-remoto"):   # también los de antes del cambio de nombre
        return None, "El archivo no es un respaldo de Mando"
    if not isinstance(data.get("format"), int) or data["format"] > FORMAT:
        return None, "El respaldo es de una versión más nueva: actualiza Mando"
    counts = {}
    for s in SECTIONS:
        v = data.get(s)
        if s == "budget":
            counts[s] = 1 if isinstance(v, dict) and v.get("limit") else 0
            continue
        counts[s] = (1 if v.get("url") else 0) if s == "jira" and isinstance(v, dict) else len(v) if isinstance(v, list) else 0
    return counts, None


def import_data(data: Any, sections: list[str], mode: str) -> tuple[dict[str, Any] | None, str | None]:
    """mode: «merge» (agregar a lo actual) o «replace». Las pestañas solo se agregan."""
    _, err = preview(data)
    if err:
        return None, err
    if mode not in ("merge", "replace"):
        return None, "Modo inválido"
    sections = [s for s in sections if s in SECTIONS]
    result: dict[str, Any] = {"added": {}, "skipped": []}

    if "rules" in sections and isinstance(data.get("rules"), list):
        rules = shared_rules()
        if mode == "replace":
            rules.clear()
        n = 0
        for r in data["rules"]:
            rule, e = validate_rule(r if isinstance(r, dict) else {})
            if e:
                result["skipped"].append(f"Regla «{(r or {}).get('pattern', '?') if isinstance(r, dict) else '?'}»: {e}")
                continue
            if any(x["action"] == rule["action"] and x["kind"] == rule["kind"] and x["pattern"] == rule["pattern"]
                   for x in rules):
                continue
            rules.append(rule)
            n += 1
        save_state(STATE)
        result["added"]["rules"] = n

    if "templates" in sections and isinstance(data.get("templates"), list):
        if mode == "replace":
            STATE["templates"] = []
        existing = {(t["name"], t["text"]) for t in tpl.templates()}
        n = 0
        for t in data["templates"]:
            if not isinstance(t, dict) or (str(t.get("name", "")).strip(), str(t.get("text", "")).strip()) in existing:
                continue
            item, e = tpl.save({k: t.get(k) for k in ("name", "text", "send")})
            if e:
                result["skipped"].append(f"Plantilla «{t.get('name', '?')}»: {e}")
                continue
            existing.add((item["name"], item["text"]))
            n += 1
        tpl.touch()
        result["added"]["templates"] = n

    if "budget" in sections and isinstance(data.get("budget"), dict):
        _, e = budget.save(data["budget"].get("limit", 0), data["budget"].get("warn", 80))
        if e:
            result["skipped"].append(f"Límite de gasto: {e}")
        else:
            result["added"]["budget"] = 1
    if "jira" in sections and isinstance(data.get("jira"), dict):
        conf, e = jira.save(data["jira"])
        if e:
            result["skipped"].append(f"Jira: {e}")
        else:
            result["added"]["jira"] = 1 if conf["url"] else 0

    if "tabs" in sections and isinstance(data.get("tabs"), list):
        names = {a.name for a in AGENTS}
        n = 0
        for t in data["tabs"]:
            if not isinstance(t, dict):
                continue
            name = str(t.get("name") or "").strip()[:MAX_TAB_NAME]
            if not name or name in names:
                continue   # ya existe una pestaña con ese nombre
            if len(AGENTS.items) >= MAX_TABS:
                result["skipped"].append(f"Pestaña «{name}»: máximo {MAX_TABS} sesiones")
                continue
            cwd = resolve_dir(str(t.get("cwd") or ""), AGENTS.first().cwd) if t.get("cwd") else None
            if t.get("cwd") and not cwd:
                result["skipped"].append(f"Pestaña «{name}»: la carpeta {t['cwd']} no existe aquí; se usa la actual")
            agent = AGENTS.create(name, cwd)
            if t.get("mode") in PERMISSION_MODES:
                agent.mode = t["mode"]
            model = str(t.get("model") or "")
            if model and model != "default":
                if model in MODELS:
                    agent.model = model
                else:
                    result["skipped"].append(f"Pestaña «{name}»: el modelo {model} no está en CLAUDE_MODELS; se usa el predeterminado")
            agent.persist()
            names.add(name)
            n += 1
        result["added"]["tabs"] = n
    return result, None
