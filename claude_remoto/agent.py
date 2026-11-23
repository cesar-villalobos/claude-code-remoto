"""Claude Code: conexión, permisos, hooks, streaming y ejecución de tareas."""

from __future__ import annotations

import asyncio
import copy
import os
import secrets
import time
from pathlib import Path
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

from . import __version__, budget, changes, usage
from .attachments import content_blocks
from .audit import REQUEST, audit
from .config import (
    AUTO_APPROVE,
    AUTO_APPROVE_PREFIXES,
    CA_CERT,
    CHANGES_SUMMARY,
    CORP_CA_BUNDLE,
    CORP_PROXY,
    DEFAULT_MODE,
    DEFAULT_WORKDIR,
    GATEWAY_VARS,
    MCP_SERVERS,
    MODEL,
    MODEL_LABELS,
    MODELS,
    NO_PROXY,
    PERMISSION_MODES,
    STREAM_INTERVAL,
    STREAM_TEXT,
    SYSTEM_APPEND,
    TLS_CERT,
    log,
)
from .decisions import CANCELLED, PENDING, Pending, tool_trace, tool_view, wait_decision
from .diff import edit_diff
from .hub import HUB
from .jira import config as jira_config
from .rules import DEFAULT_RULES, find_rule, rule_label, suggest_rule
from .state import STATE, save_state
from .templates import version as tpl_version
from .updates import update_info
from .voice import engine as voice_engine

try:  # texto en tiempo real (SDK reciente); sin él la app funciona igual, sin streaming
    from claude_agent_sdk.types import StreamEvent
except ImportError:  # pragma: no cover
    StreamEvent = None


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


def model_label(value: str) -> str:
    return MODEL_LABELS.get(value, value)


MAX_COMMANDS = 300


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


MAX_TABS = int(os.getenv("MAX_SESSIONS", "6"))   # cada sesión mantiene su propio proceso de Claude Code
MAX_TAB_NAME = 30
MAX_QUEUE = 10   # mensajes en cola por sesión


def shared_rules() -> list[dict[str, Any]]:
    """Reglas de aprobación: comunes a todas las sesiones."""
    if "rules" not in STATE:  # primera vez: reglas de bloqueo predeterminadas
        STATE["rules"] = [{"id": secrets.token_hex(4), **r} for r in copy.deepcopy(DEFAULT_RULES)]
    return STATE["rules"]


class Agent:
    """Una sesión de trabajo: su propia conexión a Claude Code, carpeta, modo y tarea."""

    def __init__(self, tab: dict[str, Any]) -> None:
        self.id: str = tab["id"]
        self.name: str = tab.get("name") or "Sesión"
        self.cwd: str = tab.get("cwd") or DEFAULT_WORKDIR
        self.session_id: str | None = tab.get("session_id")
        self.client: ClaudeSDKClient | None = None
        self.closed = False
        self.finishing = False
        self.queue: list[dict[str, Any]] = []   # mensajes enviados mientras trabajaba
        self.tracker: changes.Tracker | None = None   # archivos que cambia la tarea en curso
        self.commands_cache: list[dict[str, str]] | None = None  # comandos «/» de esta conexión
        self.lock = asyncio.Lock()
        self.task: asyncio.Task | None = None
        self.always_allow: set[str] = set()
        self.rules: list[dict[str, Any]] = shared_rules()
        saved_model = tab.get("model") or ""
        self.model: str = saved_model if saved_model in MODELS else ""  # "" = predeterminado
        saved_mode = tab.get("mode")
        self.mode: str = saved_mode if saved_mode in PERMISSION_MODES else DEFAULT_MODE
        self.started_at: float | None = None  # para el cronómetro de la animación
        # Texto parcial en curso y bloques terminados que esperan su mensaje definitivo
        self.stream: dict[str, Any] | None = None
        self.stream_msg_id: str | None = None
        self.finished_streams: list[dict[str, Any]] = []
        self.last_stream_emit = 0.0

    def emit(self, type_: str, **data: Any) -> dict[str, Any] | None:
        if self.closed:  # sesión cerrada: lo que quede de su tarea no vuelve al historial
            return None
        return HUB.emit(type_, tab=self.id, **data)

    # -- respuestas en tiempo real -----------------------------------------
    def current_stream(self) -> dict[str, Any] | None:
        return ({"tab": self.id, "sid": self.stream["sid"], "text": self.stream["text"]}
                if self.stream and self.stream["text"] else None)

    def emit_stream(self) -> None:
        if self.stream and self.stream["text"]:
            self.last_stream_emit = time.monotonic()
            self.emit("stream", sid=self.stream["sid"], text=self.stream["text"])

    def on_stream_event(self, ev: dict[str, Any]) -> None:
        """Procesa los eventos crudos de la API (message_start, content_block_*)."""
        kind = ev.get("type")
        if kind == "message_start":
            self.stream_msg_id = (ev.get("message") or {}).get("id")
        elif kind == "content_block_start" and (ev.get("content_block") or {}).get("type") == "text":
            self.stream = {"sid": secrets.token_hex(4), "text": "", "msg": self.stream_msg_id}
        elif kind == "content_block_delta" and self.stream and (ev.get("delta") or {}).get("type") == "text_delta":
            self.stream["text"] += ev["delta"].get("text", "")
            if time.monotonic() - self.last_stream_emit >= STREAM_INTERVAL:
                self.emit_stream()
        elif kind == "content_block_stop" and self.stream:
            self.emit_stream()  # último fragmento
            if self.stream["text"]:
                self.finished_streams.append(self.stream)
            self.stream = None

    def take_stream(self, message_id: str | None) -> dict[str, Any] | None:
        """Bloque parcial que corresponde al mensaje definitivo (mismo message_id, en orden)."""
        for i, st in enumerate(self.finished_streams):
            if message_id is None or st["msg"] is None or st["msg"] == message_id:
                return self.finished_streams.pop(i)
        return None

    def close_streams(self) -> None:
        """Al terminar o interrumpir: lo ya escrito queda como mensaje definitivo."""
        pending = self.finished_streams + ([self.stream] if self.stream and self.stream["text"] else [])
        for st in pending:
            self.emit("assistant", text=st["text"], replaces=st["sid"])
        self.stream, self.finished_streams, self.stream_msg_id = None, [], None

    def elapsed(self) -> float:
        """Segundos desde que empezó la tarea actual."""
        return round(time.time() - self.started_at, 1) if self.started_at else 0.0

    @property
    def busy(self) -> bool:
        # «finishing»: el estado final se publica desde dentro de la tarea, que aún no figura terminada
        return bool(self.task and not self.task.done()) and not self.finishing

    def tab_state(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "cwd": self.cwd, "session_id": self.session_id, "mode": self.mode,
                "model": self.model}

    def persist(self) -> None:
        AGENTS.persist()

    def status(self) -> dict[str, Any]:
        """Estado de esta sesión (la interfaz muestra el de la pestaña abierta)."""
        return {
            "id": self.id,
            "name": self.name,
            "busy": self.busy,
            "cwd": self.cwd,
            "session": self.session_id,
            "pending": sum(1 for p in PENDING.values() if p.tab == self.id),
            "always": sorted(self.always_allow),
            "mode": self.mode,
            "model": self.model or "default",
            "since": self.started_at,  # inicio de la tarea actual (epoch del servidor)
            "queued": len(self.queue),
        }

    # -- permisos ---------------------------------------------------------
    async def can_use_tool(self, tool_name: str, data: dict[str, Any], ctx: ToolPermissionContext):
        if tool_name == "AskUserQuestion":
            return await self.ask_questions(data)
        if tool_name in self.always_allow or any(tool_name.startswith(p) for p in AUTO_APPROVE_PREFIXES):
            return PermissionResultAllow(updated_input=data)

        if tool_name == "ExitPlanMode":
            return await self.review_plan(data)

        # Reglas: bloquear (también lo hace el hook) y aprobar siempre
        rule = find_rule(self.rules, "deny", tool_name, data, self.cwd)
        if rule:
            audit("blocked", tool=tool_name, target=tool_trace(tool_name, data), rule=rule_label(rule))
            return PermissionResultDeny(message=self.deny_message(rule))
        rule = find_rule(self.rules, "allow", tool_name, data, self.cwd)
        if rule:
            self.emit("info", text=f"⚖️ Aprobado por regla: {tool_trace(tool_name, data)} · {rule_label(rule)}")
            audit("auto_approved", tool=tool_name, target=tool_trace(tool_name, data), rule=rule_label(rule))
            return PermissionResultAllow(updated_input=data)

        pid = secrets.token_hex(6)
        PENDING[pid] = Pending(future=asyncio.get_running_loop().create_future(), kind="perm", tool_name=tool_name,
                               summary=tool_trace(tool_name, data), tab=self.id)
        title, detail = tool_view(tool_name, data)
        extra: dict[str, Any] = {}
        if tool_name in ("Edit", "MultiEdit", "Write"):
            try:
                diff = edit_diff(tool_name, data, self.cwd)
            except Exception:  # noqa: BLE001 — el diff es una ayuda visual, nunca debe bloquear
                log.exception("No se pudo calcular el diff")
                diff = None
            if diff:
                extra["diff"] = diff
                if diff["new_file"]:
                    title = f"📄 Crear archivo · {data.get('file_path', '')}"
                detail = f"+{diff['add']} −{diff['del']} líneas"
        suggestion = suggest_rule(tool_name, data, self.cwd)
        if suggestion:
            extra["suggest"] = suggestion
        self.emit("perm", ref=pid, tool=tool_name, title=title, detail=detail, **extra)
        try:
            decision = await wait_decision(pid)
        except asyncio.TimeoutError:
            self.emit("decided", ref=pid, label="⌛ Sin respuesta: rechazado")
            audit("decision", tool=tool_name, target=tool_trace(tool_name, data), result="⌛ sin respuesta")
            return PermissionResultDeny(message="El usuario no respondió a tiempo.")
        choice, comment = (decision.get("choice"), decision.get("comment", "")) if isinstance(decision, dict) \
            else (decision, "")
        if choice == "a":
            self.always_allow.add(tool_name)
        if choice in ("y", "a"):
            return PermissionResultAllow(updated_input=data)
        if comment:
            # Claude recibe el motivo y puede corregir el rumbo en vez de detenerse
            return PermissionResultDeny(message=(
                f"El usuario rechazó esta acción y comentó: «{comment}». "
                "Ten en cuenta su comentario y propón otra forma de continuar."))
        return PermissionResultDeny(message="El usuario rechazó esta acción.")

    def deny_message(self, rule: dict[str, Any]) -> str:
        return (f"Bloqueado por una regla del usuario ({rule_label(rule)}"
                + (f": {rule['note']}" if rule.get("note") else "") + "). "
                "No intentes lograr lo mismo de otra forma; si es imprescindible, explícale al usuario por qué.")

    async def pre_tool_hook(self, hook_input: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        """Hook PreToolUse: se ejecuta antes de cada herramienta, en cualquier modo de permisos."""
        tool_name = hook_input.get("tool_name", "")
        data = hook_input.get("tool_input") or {}
        rule = find_rule(self.rules, "deny", tool_name, data, self.cwd)
        if not rule:
            if self.tracker:   # contenido original, para el resumen de cambios al terminar
                await asyncio.to_thread(self.tracker.before_tool, tool_name, data)
            return {}
        self.emit("info", text=f"⛔ Bloqueado por regla: {tool_trace(tool_name, data)} · {rule_label(rule)}")
        audit("blocked", tool=tool_name, target=tool_trace(tool_name, data), rule=rule_label(rule))
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": self.deny_message(rule)}}

    async def review_plan(self, data: dict[str, Any]):
        """Claude terminó de planificar: aprobar (y en qué modo seguir) o pedir cambios."""
        pid = secrets.token_hex(6)
        PENDING[pid] = Pending(future=asyncio.get_running_loop().create_future(), kind="perm", tool_name="ExitPlanMode",
                               summary="plan propuesto", tab=self.id)
        plan = str(data.get("plan") or "")
        self.emit("perm", ref=pid, tool="ExitPlanMode", title="📋 Plan propuesto", detail="Revisa el plan de Claude",
                 plan=plan[:20000])
        try:
            decision = await wait_decision(pid)
        except asyncio.TimeoutError:
            self.emit("decided", ref=pid, label="⌛ Sin respuesta: rechazado")
            return PermissionResultDeny(message="El usuario no respondió a tiempo.")
        choice, comment = (decision.get("choice"), decision.get("comment", "")) if isinstance(decision, dict) \
            else (decision, "")
        if choice in ("y", "e"):
            # Al aprobar, Claude sale del modo plan. El cambio de modo se agenda aparte: no se puede
            # esperar una respuesta del CLI mientras este espera la nuestra.
            new_mode = "acceptEdits" if choice == "e" else "default"
            asyncio.get_running_loop().call_later(0.2, lambda: asyncio.create_task(self.set_mode(new_mode)))
            return PermissionResultAllow(updated_input=data)
        if comment:
            return PermissionResultDeny(message=(
                f"El usuario no aprobó el plan y pidió cambios: «{comment}». "
                "Ajusta el plan según su comentario y vuelve a presentarlo."))
        return PermissionResultDeny(message="El usuario no aprobó el plan. Sigue en modo plan.")

    async def ask_questions(self, data: dict[str, Any]):
        answers: dict[str, str] = {}
        for q in data.get("questions", []):
            pid = secrets.token_hex(6)
            opts = q.get("options", [])
            PENDING[pid] = Pending(
                future=asyncio.get_running_loop().create_future(), kind="question", summary=q.get("question", ""),
                options=[o.get("label", "") for o in opts], multi=bool(q.get("multiSelect")), tab=self.id,
            )
            self.emit(
                "question", ref=pid, header=q.get("header", "Decisión"), question=q.get("question", ""),
                options=[{"label": o.get("label", ""), "description": o.get("description", "")} for o in opts],
                multi=PENDING[pid].multi,
            )
            try:
                answer = await wait_decision(pid)
            except asyncio.TimeoutError:
                self.emit("decided", ref=pid, label="⌛ Sin respuesta")
                return PermissionResultDeny(message="El usuario no respondió la pregunta a tiempo.")
            if answer == CANCELLED:
                return PermissionResultDeny(message="El usuario canceló la tarea.")
            answers[q.get("question", "")] = answer
        return PermissionResultAllow(updated_input={"questions": data.get("questions", []), "answers": answers})

    # -- ciclo de vida ----------------------------------------------------
    def options(self) -> ClaudeAgentOptions:
        kwargs: dict[str, Any] = dict(
            cwd=self.cwd,
            permission_mode=self.mode,
            allowed_tools=AUTO_APPROVE,
            can_use_tool=self.can_use_tool,
            setting_sources=["user", "project", "local"],
            mcp_servers=MCP_SERVERS,  # además de los configurados en Claude Code (claude mcp add)
            system_prompt={"type": "preset", "preset": "claude_code", "append": SYSTEM_APPEND},
            env=claude_env(),
            include_partial_messages=STREAM_TEXT and StreamEvent is not None,
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[self.pre_tool_hook])]},
        )
        if self.session_id:
            kwargs["resume"] = self.session_id
        model = self.model if self.model not in ("", "default") else MODEL
        if model:
            kwargs["model"] = model
        return ClaudeAgentOptions(**kwargs)

    async def commands(self) -> list[dict[str, str]]:
        """Comandos «/» que ofrece Claude Code (integrados, .claude/commands, skills, prompts MCP)."""
        if self.commands_cache is None:
            if self.client is None:
                async with self.lock:  # sin tarea en curso: conectar no consume tokens
                    if self.client is None:
                        self.client = ClaudeSDKClient(options=self.options())
                        await self.client.connect()
            info = await self.client.get_server_info() or {}
            self.commands_cache = normalize_commands(info.get("commands") or [])
        return self.commands_cache

    async def close(self) -> None:
        self.commands_cache = None  # otra carpeta o sesión puede tener otros comandos
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:  # noqa: BLE001
                log.exception("Error cerrando cliente")
            self.client = None

    async def stop(self) -> None:
        self.clear_queue("⏹️ Quitado: se detuvo la tarea")   # detener = retomar el control
        if self.client:
            try:
                await self.client.interrupt()
            except Exception:  # noqa: BLE001
                pass
        for pid, p in list(PENDING.items()):
            if p.tab == self.id and not p.future.done():
                p.future.set_result(CANCELLED)
                self.emit("decided", ref=pid, label="⏹️ Cancelado")
        if self.task and not self.task.done():
            self.task.cancel()

    async def set_mode(self, mode: str, *, announce: bool = True) -> None:
        """Cambia el modo de permisos; si hay una sesión abierta, también en caliente."""
        self.mode = mode
        self.persist()
        if self.client is not None:
            try:
                await self.client.set_permission_mode(mode)
            except Exception:  # noqa: BLE001 — se aplicará al abrir la próxima sesión
                log.exception("No se pudo cambiar el modo en la sesión actual")
        audit("mode", mode=mode, tab=self.name)
        if announce:
            self.emit("info", text=f"Modo: {PERMISSION_MODES[mode]}")
        emit_status()

    async def set_model(self, model: str) -> None:
        """Cambia el modelo de esta sesión; si está conectada, también en caliente."""
        self.model = "" if model == "default" else model
        self.persist()
        if self.client is not None:
            try:
                await self.client.set_model(self.model or MODEL)
            except Exception:  # noqa: BLE001 — se aplicará al abrir la próxima sesión
                log.exception("No se pudo cambiar el modelo en la sesión actual")
        audit("model", model=model, tab=self.name)
        self.emit("info", text=f"🧠 Modelo: {model_label(model)}")
        emit_status()

    async def reset(self, *, session_id: str | None = None, cwd: str | None = None) -> None:
        await self.stop()
        await self.close()
        self.session_id = session_id
        if cwd:
            self.cwd = cwd
        self.always_allow.clear()
        self.persist()

    def submit(self, prompt: str, images: list[dict[str, Any]] | None = None) -> str:
        """«started», «queued» (Claude está trabajando: va a la cola), «full» (cola llena)
        o «limit» (se alcanzó el límite de gasto mensual)."""
        images = images or []
        if budget.blocked():
            return "limit"
        thumbs = [i["thumb"] for i in images]
        if self.busy:
            if len(self.queue) >= MAX_QUEUE:
                return "full"
            qid = secrets.token_hex(4)
            self.queue.append({"qid": qid, "text": prompt, "images": images})
            self.emit("queued", qid=qid, text=prompt, **({"images": thumbs} if images else {}))
            emit_status()
            return "queued"
        self.start(prompt, images)
        return "started"

    def start(self, prompt: str, images: list[dict[str, Any]], qid: str | None = None) -> None:
        thumbs = [i["thumb"] for i in images]
        # en el historial: solo miniaturas; «qid» indica que salió de la cola
        self.emit("user", text=prompt, **({"images": thumbs} if images else {}), **({"qid": qid} if qid else {}))
        self.finishing = False
        self.task = asyncio.create_task(self.run(prompt, images))

    def next_from_queue(self) -> None:
        """Al terminar una tarea, sigue con el primer mensaje de la cola."""
        if self.closed or not self.queue:
            return
        if self.task and not self.task.done():   # todavía cerrando la anterior
            asyncio.get_running_loop().call_later(0.05, self.next_from_queue)
            return
        if budget.blocked():   # se alcanzó el límite con la tarea anterior: no se inicia lo que quedó en cola
            self.clear_queue("⛔ No se envió: se alcanzó el límite de gasto mensual")
            return
        item = self.queue.pop(0)
        self.start(item["text"], item["images"], qid=item["qid"])

    def unqueue(self, qid: str, label: str) -> bool:
        item = next((q for q in self.queue if q["qid"] == qid), None)
        if item is None:
            return False
        self.queue.remove(item)
        self.emit("unqueued", qid=qid, label=label)
        emit_status()
        return True

    def clear_queue(self, label: str) -> None:
        for item in list(self.queue):
            self.unqueue(item["qid"], label)

    async def emit_changes(self) -> None:
        """Al terminar (bien, con error o detenida): archivos que cambiaron en esta tarea."""
        tracker, self.tracker = self.tracker, None
        if not tracker:
            return
        try:
            summary = await asyncio.to_thread(tracker.summary)
        except Exception:  # noqa: BLE001
            log.exception("No se pudo armar el resumen de cambios")
            return
        if summary:
            self.emit("changes", **summary)
            audit("changes", tab=self.name, files=summary["total"], add=summary["add"], removed=summary["del"])

    async def run(self, prompt: str, images: list[dict[str, Any]] | None = None) -> None:
        # La tarea corre en su propio contexto: lo que haga Claude no se atribuye al dispositivo
        # que la envió (las decisiones sí, porque llegan por su propia petición)
        REQUEST.set(None)
        async with self.lock:
            self.started_at = time.time()
            emit_status()
            self.tracker = await asyncio.to_thread(changes.Tracker, self.cwd) if CHANGES_SUMMARY else None
            try:
                if self.client is None:
                    self.client = ClaudeSDKClient(options=self.options())
                    await self.client.connect()
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
                            self.on_stream_event(msg.event)
                        continue
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock) and block.text.strip():
                                st = None if msg.parent_tool_use_id else self.take_stream(msg.message_id)
                                # el texto definitivo reemplaza a la burbuja parcial
                                self.emit("assistant", text=block.text, **({"replaces": st["sid"]} if st else {}))
                            elif isinstance(block, ToolUseBlock):
                                self.emit("tool", text=tool_trace(block.name, block.input or {}))
                                audit("tool", tool=block.name, target=tool_trace(block.name, block.input or {}))
                    elif isinstance(msg, ResultMessage):
                        if getattr(msg, "session_id", None):
                            self.session_id = msg.session_id
                            self.persist()
                        u = getattr(msg, "usage", None) or {}
                        models = list(getattr(msg, "model_usage", None) or {})
                        before = budget.info()
                        rec = usage.record(tab=self.name, tab_id=self.id, cost=getattr(msg, "total_cost_usd", None),
                                           usage=u, duration=self.elapsed(), turns=getattr(msg, "num_turns", None),
                                           model=models[0] if models else (getattr(self, "model", "") or MODEL or "default"),
                                           ok=not msg.is_error)
                        self.emit("done", ok=not msg.is_error, cost=getattr(msg, "total_cost_usd", None),
                                  tokens=rec["in"] + rec["out"],
                                  duration=self.elapsed())  # para avisar al terminar tareas largas
                        notice = budget.crossed(before, budget.info())
                        if notice:
                            HUB.emit("info", text=notice)   # en todas las pestañas
                            emit_status()
            except asyncio.CancelledError:
                self.emit("info", text="⏹️ Interrumpido.")
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("Fallo en el turno")
                self.emit("error", text=repr(e), duration=self.elapsed())
                await self.close()
            finally:
                self.close_streams()
                await self.emit_changes()
                self.started_at = None
                self.finishing = True
                emit_status()
                if self.queue:
                    asyncio.get_running_loop().call_soon(self.next_from_queue)



class Agents:
    """Registro de sesiones paralelas (pestañas). Se guardan en el estado y sobreviven a reinicios."""

    def __init__(self) -> None:
        tabs = [t for t in STATE.get("tabs") or [] if isinstance(t, dict) and t.get("id")]
        if not tabs:  # primera vez, o versión anterior con una sola sesión
            tabs = [{"id": secrets.token_hex(4), "name": "Principal", "cwd": STATE.get("cwd") or DEFAULT_WORKDIR,
                     "session_id": STATE.get("session_id"), "mode": STATE.get("mode")}]
        self.items: dict[str, Agent] = {t["id"]: Agent(t) for t in tabs[:MAX_TABS]}

    def __iter__(self):
        return iter(list(self.items.values()))

    def first(self) -> Agent:
        return next(iter(self.items.values()))

    def get(self, tab_id: Any = None) -> Agent | None:
        """Sesión pedida, o la primera si no se indica ninguna (compatibilidad)."""
        return self.first() if not tab_id else self.items.get(str(tab_id))

    def persist(self) -> None:
        STATE["tabs"] = [a.tab_state() for a in self.items.values()]
        for k in ("cwd", "session_id", "mode"):  # formato anterior: ya no se usa
            STATE.pop(k, None)
        save_state(STATE)

    def create(self, name: str, cwd: str | None = None) -> Agent:
        agent = Agent({"id": secrets.token_hex(4), "name": name, "cwd": cwd or self.first().cwd,
                       "mode": self.first().mode})
        self.items[agent.id] = agent
        self.persist()
        return agent

    async def close(self, tab_id: str) -> Agent | None:
        agent = self.items.get(tab_id)
        if agent is None or len(self.items) == 1:
            return None
        await agent.stop()
        if agent.task and not agent.task.done():
            await asyncio.wait([agent.task], timeout=5)
        agent.closed = True
        await agent.close()
        del self.items[tab_id]
        self.persist()
        return agent

    def streams(self) -> list[dict[str, Any]]:
        return [st for a in self if (st := a.current_stream())]


def unique_name(base: str) -> str:
    names = {a.name for a in AGENTS}
    if base not in names:
        return base
    n = 2
    while f"{base} {n}" in names:
        n += 1
    return f"{base} {n}"


def global_status(tab_id: Any = None) -> dict[str, Any]:
    """Estado para la interfaz: lo global, la lista de sesiones y, arriba, el de la sesión pedida."""
    agent = AGENTS.get(tab_id) or AGENTS.first()
    return {
        **{k: v for k, v in agent.status().items() if k != "id"},   # «id» es el del evento, no el de la sesión
        "tab": agent.id,
        "tabs": [a.status() for a in AGENTS],
        "max_tabs": MAX_TABS,
        "models": [{"value": m, "label": model_label(m)} for m in MODELS],
        "version": __version__,
        "update": update_info(),   # versión nueva disponible, si la hay
        "budget": budget.info(),   # límite de gasto mensual
        "gateway": gateway_host() or "settings de Claude Code",
        "voice": voice_engine(),  # whisper | browser | off
        "tpl": tpl_version(),     # versión de las plantillas: si cambia, la interfaz las recarga
        "jira": jira_config(),    # dirección y proyectos para enlazar claves como ABC-123
        "secure": bool(TLS_CERT),  # HTTPS activo (necesario para notificaciones en el teléfono)
        "ca": bool(CA_CERT and Path(CA_CERT).is_file()),  # hay CA descargable en /ca.crt
        "now": time.time(),        # para que el cliente corrija el desfase de reloj
    }


def emit_status() -> None:
    HUB.emit("status", **global_status())


AGENTS = Agents()

