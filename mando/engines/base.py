"""Contrato común de los motores (el agente de programación que hay detrás de cada pestaña).

Un motor sabe hablar con un agente concreto (Claude Code, Codex…). El resto de Mando (pestañas,
cola, tarjetas, reglas, burbujas en vivo, resumen de cambios, costo) no sabe cuál hay detrás:

- Mando → motor: `run()` un turno, `interrupt()`, `close()`, `set_mode()`, `set_model()`, `commands()`.
- Motor → Mando (el «host», la pestaña): pide decisiones y entrega lo que va pasando.

Las herramientas se nombran como en Claude Code («Bash» con {"command"}, «Edit»/«Write» con
{"file_path"}, «mcp__servidor__herramienta», «AskUserQuestion», «ExitPlanMode»…): es el idioma común
con el que funcionan las reglas, los diffs, la auditoría y el resumen de cambios. Un motor que use
otros nombres los traduce antes de pedir la decisión.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Decision:
    """Respuesta de Mando a una petición de herramienta."""
    allow: bool
    message: str = ""                       # al rechazar: lo que se le explica al agente
    interrupt: bool = False                 # además, detener el turno
    updated_input: dict[str, Any] | None = None   # p. ej. las respuestas de AskUserQuestion


@dataclass
class TurnResult:
    """Lo que informa el agente al terminar un turno."""
    is_error: bool = False
    session_id: str | None = None
    cost: float | None = None               # US$ estimados, si el motor lo informa
    cost_cumulative: bool = False           # el costo es el total acumulado de la sesión, no el del turno
    usage: dict[str, Any] = field(default_factory=dict)   # input_tokens, output_tokens, cache_*
    turns: int | None = None
    model: str | None = None


class Host(Protocol):
    """Lo que la pestaña ofrece a su motor."""
    id: str
    cwd: str
    mode: str
    model: str
    session_id: str | None
    stopping: bool

    # decisiones (pueden esperar a que respondas en el celular)
    async def decide_tool(self, tool_name: str, data: dict[str, Any]) -> Decision: ...
    async def pre_tool(self, tool_name: str, data: dict[str, Any]) -> str | None: ...   # motivo si se bloquea

    # lo que va pasando
    def stream_start(self, message_id: str | None) -> None: ...
    def stream_delta(self, text: str) -> None: ...
    def stream_stop(self) -> None: ...
    def final_text(self, text: str, message_id: str | None = None, subagent: bool = False) -> None: ...
    def tool_used(self, tool_name: str, data: dict[str, Any]) -> None: ...
    def turn_result(self, result: TurnResult) -> None: ...


class Engine:
    """Base de los motores. Cada uno define sus capacidades y su forma de hablar con el agente."""

    name = "base"
    label = "Motor"
    # Lo que la interfaz puede ofrecer con este motor
    capabilities: dict[str, Any] = {
        "modes": [],          # modos de permisos que entiende (claves de PERMISSION_MODES)
        "plan": False,        # modo «solo planificar» con tarjeta de plan
        "commands": False,    # comandos «/»
        "images": False,      # capturas adjuntas
        "cost": False,        # informa el costo en US$
        "resume": False,      # retomar conversaciones anteriores
    }

    def __init__(self, host: Host) -> None:
        self.host = host

    @property
    def connected(self) -> bool:
        return False

    async def run(self, prompt: str, images: list[dict[str, Any]] | None = None) -> None:
        """Ejecuta un turno completo, avisando al host lo que pasa. Termina al terminar el turno."""
        raise NotImplementedError

    async def interrupt(self) -> None:
        """Pide al agente que deje lo que está haciendo (lo que ya corrió no se deshace)."""

    async def close(self) -> None:
        """Cierra la conexión (al cambiar de carpeta, de sesión, o al cerrar la pestaña)."""

    async def set_mode(self, mode: str) -> None:
        """Aplica el modo de permisos en caliente, si hay una conexión abierta."""

    async def set_model(self, model: str) -> None:
        """Aplica el modelo en caliente, si hay una conexión abierta."""

    async def commands(self) -> list[dict[str, str]]:
        """Comandos «/» disponibles: [{name, description, hint}]."""
        return []
