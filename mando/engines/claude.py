"""Motor Claude Code: habla con el CLI de Claude Code mediante claude-agent-sdk."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)
from claude_agent_sdk.types import (
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from ..attachments import content_blocks
from ..config import (
    AUTO_APPROVE,
    CORP_CA_BUNDLE,
    CORP_PROXY,
    GATEWAY_VARS,
    MCP_SERVERS,
    MODEL,
    MODELS,
    NO_PROXY,
    PERMISSION_MODES,
    STREAM_TEXT,
    SYSTEM_APPEND,
    log,
)
from .base import Engine, Host, TurnResult

try:  # texto en tiempo real (SDK reciente); sin él la app funciona igual, sin streaming
    from claude_agent_sdk.types import StreamEvent
except ImportError:  # pragma: no cover
    StreamEvent = None

MAX_COMMANDS = 300


def gateway_host() -> str | None:
    url = os.getenv("ANTHROPIC_BASE_URL")
    return urlparse(url).hostname if url else None


def claude_env() -> dict[str, str]:
    env: dict[str, str] = {k: os.environ[k] for k in GATEWAY_VARS if os.getenv(k)}
    no_proxy = [h for h in NO_PROXY.split(",") if h]
    if CORP_PROXY:
        env.update(HTTPS_PROXY=CORP_PROXY, HTTP_PROXY=CORP_PROXY)
        if (host := gateway_host()) and host not in no_proxy:
            no_proxy.append(host)
    if no_proxy:
        env["NO_PROXY"] = ",".join(no_proxy)
    if CORP_CA_BUNDLE:
        env["NODE_EXTRA_CA_CERTS"] = os.path.expanduser(CORP_CA_BUNDLE)
    return env


def normalize_commands(raw: list[Any]) -> list[dict[str, str]]:
    """Lista de comandos del CLI → [{name, description, hint}], sin «/» y ordenada."""
    out: dict[str, dict[str, str]] = {}
    for c in raw:
        if isinstance(c, str):
            c = {"name": c}
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip().lstrip("/")
        if not name or len(name) > 80 or any(ch.isspace() for ch in name):
            continue
        out[name] = {"name": name,
                     "description": str(c.get("description") or "").strip()[:200],
                     "hint": str(c.get("argumentHint") or c.get("argument_hint") or "").strip()[:80]}
    return sorted(out.values(), key=lambda c: c["name"].lower())[:MAX_COMMANDS]


class ClaudeEngine(Engine):
    name = "claude"
    label = "Claude Code"
    capabilities = {
        "modes": list(PERMISSION_MODES),
        "plan": True,
        "commands": True,
        "images": True,
        "cost": True,
        "resume": True,
    }
    models = MODELS

    def __init__(self, host: Host) -> None:
        super().__init__(host)
        self.client: ClaudeSDKClient | None = None
        self._msg_id: str | None = None   # mensaje en curso (texto en tiempo real)

    @property
    def connected(self) -> bool:
        return self.client is not None

    # -- conexión ---------------------------------------------------------
    def options(self) -> ClaudeAgentOptions:
        h = self.host
        kwargs: dict[str, Any] = dict(
            cwd=h.cwd,
            permission_mode=h.mode,
            allowed_tools=AUTO_APPROVE,
            can_use_tool=self._can_use_tool,
            setting_sources=["user", "project", "local"],
            mcp_servers=MCP_SERVERS,  # además de los configurados en Claude Code (claude mcp add)
            system_prompt={"type": "preset", "preset": "claude_code", "append": SYSTEM_APPEND},
            env=claude_env(),
            include_partial_messages=STREAM_TEXT and StreamEvent is not None,
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[self._pre_tool_hook])]},
        )
        if h.session_id:
            kwargs["resume"] = h.session_id
        model = h.model if h.model not in ("", "default") else MODEL
        if model:
            kwargs["model"] = model
        return ClaudeAgentOptions(**kwargs)

    async def _connect(self) -> None:
        if self.client is None:
            self.client = ClaudeSDKClient(options=self.options())
            await self.client.connect()

    async def close(self) -> None:
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:  # noqa: BLE001
                log.exception("Error cerrando cliente")
            self.client = None

    async def interrupt(self) -> None:
        if self.client:
            try:
                await self.client.interrupt()
            except Exception:  # noqa: BLE001
                pass

    async def set_mode(self, mode: str) -> None:
        if self.client is not None:
            await self.client.set_permission_mode(mode)

    async def set_model(self, model: str) -> None:
        if self.client is not None:
            await self.client.set_model(model or MODEL)

    async def commands(self) -> list[dict[str, str]]:
        """Comandos «/» que ofrece Claude Code (integrados, .claude/commands, skills, prompts MCP)."""
        if self.client is None:
            async with self.host.lock:  # sin tarea en curso: conectar no consume tokens
                await self._connect()
        info = await self.client.get_server_info() or {}
        return normalize_commands(info.get("commands") or [])

    # -- decisiones ---------------------------------------------------------
    async def _can_use_tool(self, tool_name: str, data: dict[str, Any], ctx: ToolPermissionContext):
        d = await self.host.decide_tool(tool_name, data)
        if d.allow:
            return PermissionResultAllow(updated_input=d.updated_input if d.updated_input is not None else data)
        return PermissionResultDeny(message=d.message, interrupt=d.interrupt)

    async def _pre_tool_hook(self, hook_input: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        """Hook PreToolUse: se ejecuta antes de cada herramienta, en cualquier modo de permisos."""
        reason = await self.host.pre_tool(hook_input.get("tool_name", ""), hook_input.get("tool_input") or {})
        if reason is None:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}

    # -- un turno -------------------------------------------------------------
    def _stream(self, ev: dict[str, Any]) -> None:
        """Eventos crudos de la API (message_start, content_block_*) → texto en tiempo real."""
        kind = ev.get("type")
        if kind == "message_start":
            self._msg_id = (ev.get("message") or {}).get("id")
        elif kind == "content_block_start" and (ev.get("content_block") or {}).get("type") == "text":
            self.host.stream_start(self._msg_id)
        elif kind == "content_block_delta" and (ev.get("delta") or {}).get("type") == "text_delta":
            self.host.stream_delta(ev["delta"].get("text", ""))
        elif kind == "content_block_stop":
            self.host.stream_stop()

    async def run(self, prompt: str, images: list[dict[str, Any]] | None = None) -> None:
        await self._connect()
        if images:  # capturas adjuntas: un mensaje con bloques de imagen y de texto
            async def message():
                yield {"type": "user", "parent_tool_use_id": None,
                       "message": {"role": "user", "content": content_blocks(prompt, images)}}
            await self.client.query(message())
            images.clear()  # no retener las imágenes en memoria más de lo necesario
        else:
            await self.client.query(prompt)
        async for msg in self.client.receive_response():
            if StreamEvent is not None and isinstance(msg, StreamEvent):
                if not msg.parent_tool_use_id:  # solo el agente principal, no subagentes
                    self._stream(msg.event)
                continue
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        self.host.final_text(block.text, msg.message_id, subagent=bool(msg.parent_tool_use_id))
                    elif isinstance(block, ToolUseBlock):
                        self.host.tool_used(block.name, block.input or {})
            elif isinstance(msg, ResultMessage):
                models = list(getattr(msg, "model_usage", None) or {})
                self.host.turn_result(TurnResult(
                    is_error=bool(msg.is_error), session_id=getattr(msg, "session_id", None),
                    # total_cost_usd es acumulado: cada resultado trae el total de la sesión
                    # hasta ahí (y una sesión retomada parte del total que guardó su transcript)
                    cost=getattr(msg, "total_cost_usd", None), cost_cumulative=True, usage=getattr(msg, "usage", None) or {},
                    turns=getattr(msg, "num_turns", None), model=models[0] if models else None))
