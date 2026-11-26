"""Motores: el agente de programación de cada pestaña (Claude Code o, en modo experimental, Codex)."""

from __future__ import annotations

from .base import Decision, Engine, Host, TurnResult
from .claude import ClaudeEngine
from .codex import CodexEngine

ENGINES: dict[str, type[Engine]] = {ClaudeEngine.name: ClaudeEngine, CodexEngine.name: CodexEngine}
DEFAULT_ENGINE = ClaudeEngine.name


def engine_class(name: str | None) -> type[Engine]:
    """Clase del motor pedido; si no existe (p. ej. una pestaña guardada con otro motor), el predeterminado."""
    return ENGINES.get(name or "", ENGINES[DEFAULT_ENGINE])


def available_engines() -> list[dict]:
    """Motores que se pueden usar en este equipo, para la interfaz."""
    return [{"name": c.name, "label": c.label, "capabilities": c.capabilities, "models": c.models}
            for c in ENGINES.values() if c.available()]


__all__ = ["DEFAULT_ENGINE", "ENGINES", "Decision", "Engine", "Host", "TurnResult", "available_engines",
           "engine_class"]
