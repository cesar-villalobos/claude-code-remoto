"""Plantillas de instrucciones frecuentes, compartidas entre todos los dispositivos.

Cada plantilla tiene un nombre corto (el botón), el texto y si se envía directo o se
inserta en el cuadro para completarla. Los campos entre llaves, como {ticket}, quedan
seleccionados en la interfaz para escribirlos o dictarlos.
"""

from __future__ import annotations

import copy
import re
import secrets
from typing import Any

from .state import STATE, save_state

MAX_TEMPLATES = 30
MAX_NAME = 40
MAX_TEXT = 2000
FIELD = re.compile(r"\{[^{}\n]{1,40}\}")
# Cambia con cada modificación: va en el estado, que llega a todos los dispositivos (SSE o sondeo)
_version = secrets.token_hex(4)


def version() -> str:
    return _version


def touch() -> None:
    """Avisa a los dispositivos que la lista cambió (p. ej. tras importar un respaldo)."""
    _changed()


def _changed() -> None:
    global _version
    _version = secrets.token_hex(4)
    save_state(STATE)

DEFAULT_TEMPLATES = [
    {"name": "🎫 Revisar ticket", "send": False,
     "text": "Revisa el ticket {ticket} en Jira, resume qué pide y propón un plan antes de cambiar código."},
    {"name": "🧪 Correr pruebas", "send": True,
     "text": "Corre las pruebas del proyecto. Si alguna falla, explica por qué y propón la corrección."},
    {"name": "🌿 Estado de git", "send": True,
     "text": "Muéstrame el estado de git: rama actual, cambios sin commit y los últimos 5 commits."},
    {"name": "✅ Hacer commit", "send": True,
     "text": "Revisa los cambios pendientes y haz un commit con un mensaje claro en español."},
    {"name": "💥 Explicar error", "send": False,
     "text": "Explica este error, su causa probable y cómo resolverlo: {error}"},
]


def templates() -> list[dict[str, Any]]:
    if "templates" not in STATE:  # primera vez: plantillas de ejemplo (se pueden borrar)
        STATE["templates"] = [{"id": secrets.token_hex(4), **t} for t in copy.deepcopy(DEFAULT_TEMPLATES)]
        save_state(STATE)
    return STATE["templates"]


def has_fields(text: str) -> bool:
    return bool(FIELD.search(text))


def validate(body: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    name = str(body.get("name") or "").strip()
    text = str(body.get("text") or "").strip()
    if not name or not text:
        return None, "Falta el nombre o el texto"
    if len(name) > MAX_NAME:
        return None, f"El nombre admite hasta {MAX_NAME} caracteres"
    if len(text) > MAX_TEXT:
        return None, f"El texto admite hasta {MAX_TEXT} caracteres"
    send = bool(body.get("send")) and not has_fields(text)  # con campos por completar no se envía directo
    return {"name": name, "text": text, "send": send}, None


def save(body: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    data, err = validate(body)
    if err:
        return None, err
    items = templates()
    tid = str(body.get("id") or "")
    if tid:
        current = next((t for t in items if t["id"] == tid), None)
        if current is None:
            return None, "Plantilla inexistente"
        current.update(data)
    else:
        if len(items) >= MAX_TEMPLATES:
            return None, f"Máximo {MAX_TEMPLATES} plantillas"
        current = {"id": secrets.token_hex(4), **data}
        items.append(current)
    _changed()
    return current, None


def delete(tid: str) -> dict[str, Any] | None:
    items = templates()
    current = next((t for t in items if t["id"] == tid), None)
    if current:
        items.remove(current)
        _changed()
    return current


def move(tid: str, delta: int) -> bool:
    items = templates()
    i = next((n for n, t in enumerate(items) if t["id"] == tid), None)
    if i is None:
        return False
    j = max(0, min(len(items) - 1, i + (1 if delta > 0 else -1)))
    items[i], items[j] = items[j], items[i]
    _changed()
    return True


def reset() -> list[dict[str, Any]]:
    STATE.pop("templates", None)
    items = templates()
    _changed()
    return items
