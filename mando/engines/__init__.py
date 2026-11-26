"""Motores disponibles: el agente de programación de cada pestaña (hoy Claude Code)."""

from __future__ import annotations

from .base import Decision, Engine, Host, TurnResult
from .claude import ClaudeEngine

ENGINES: dict[str, type[Engine]] = {ClaudeEngine.name: ClaudeEngine}
DEFAULT_ENGINE = ClaudeEngine.name


def engine_class(name: str | None) -> type[Engine]:
    """Clase del motor pedido; si no existe (p. ej. una pestaña guardada con otro motor), el predeterminado."""
    return ENGINES.get(name or "", ENGINES[DEFAULT_ENGINE])


__all__ = ["DEFAULT_ENGINE", "ENGINES", "Decision", "Engine", "Host", "TurnResult", "engine_class"]
