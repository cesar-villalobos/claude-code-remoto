"""Motor Codex (experimental): habla con `codex app-server` por JSON-RPC (una línea JSON por mensaje, stdio).

No necesita el SDK de Python de Codex: Mando lanza `codex app-server` y le habla directamente.
- Un proceso por pestaña; la conversación es un «thread» de Codex (se guarda para retomarla).
- Cada turno: `turn/start`; se leen `item/*` y `turn/completed`.
- Las aprobaciones que pide Codex (comandos y cambios de archivos) se traducen al idioma común de
  Mando («Bash» y «ApplyPatch») y pasan por las mismas reglas y tarjetas que con Claude Code.
- Codex no informa costo en US$: se registran los tokens.

Limitación: lo que Codex decide ejecutar sin pedir aprobación (según su política) no pasa por las
reglas de Mando; por eso el modo «Preguntar» usa la política más estricta («untrusted»).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
from typing import Any

from .. import __version__
from ..config import log
from .base import Decision, Engine, Host, TurnResult

CODEX_CMD = os.getenv("CODEX_CMD", "codex app-server")
CODEX_MODELS = [m.strip() for m in os.getenv("CODEX_MODELS", "default").split(",") if m.strip()] or ["default"]
# Modo de Mando → (approvalPolicy, sandbox) de Codex. Se pueden forzar con CODEX_APPROVAL_POLICY / CODEX_SANDBOX.
MODES = {
    "default": ("untrusted", "workspace-write"),       # 🛡️ Preguntar: pide aprobación para casi todo
    "acceptEdits": ("on-request", "workspace-write"),  # ✏️ Aceptar ediciones: edita en la carpeta sin preguntar
    "plan": ("untrusted", "read-only"),                # 📋 Solo planificar: no puede escribir
}
READ_LIMIT = 32 * 1024 * 1024   # líneas largas (diffs grandes)
RPC_TIMEOUT = 60


class CodexError(RuntimeError):
    pass


def available() -> bool:
    """Hay un Codex para usar: CODEX_CMD definido o el comando `codex` instalado."""
    import shutil
    return bool(os.getenv("CODEX_CMD")) or shutil.which("codex") is not None


PROXY_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE")


def codex_env() -> dict[str, str]:
    """Entorno de Codex: sin el proxy ni el certificado de Claude (CORP_PROXY y CORP_CA_BUNDLE son
    para el gateway de Claude y pueden no servir hacia OpenAI: Codex fallaba con «workspace routing
    discovery failed»). Si Codex necesita un proxy propio, se define con CODEX_PROXY,
    CODEX_NO_PROXY y CODEX_CA_BUNDLE."""
    env = {k: v for k, v in os.environ.items() if k.upper() not in PROXY_VARS}
    proxy = os.getenv("CODEX_PROXY")
    if proxy:
        env.update(HTTPS_PROXY=proxy, HTTP_PROXY=proxy)
    if os.getenv("CODEX_NO_PROXY"):
        env["NO_PROXY"] = os.environ["CODEX_NO_PROXY"]
    if os.getenv("CODEX_CA_BUNDLE"):
        env["SSL_CERT_FILE"] = os.path.expanduser(os.environ["CODEX_CA_BUNDLE"])
    return env


def command_text(cmd: Any) -> str:
    if isinstance(cmd, list):
        # Codex suele envolver en ["bash", "-lc", "…"]: se muestra el comando real
        if len(cmd) == 3 and cmd[0] in ("bash", "sh", "zsh") and cmd[1] in ("-lc", "-c"):
            return str(cmd[2])
        return shlex.join(str(c) for c in cmd)
    return str(cmd or "")


def normalize_changes(changes: Any) -> list[dict[str, Any]]:
    """[{path, kind, diff, move_path}] desde la lista (o el diccionario por ruta) que entrega Codex."""
    out = []
    items = changes.items() if isinstance(changes, dict) else ((None, c) for c in (changes or []))
    for path, c in items:
        c = c if isinstance(c, dict) else {}
        kind = c.get("kind") or c.get("type") or "update"
        if isinstance(kind, dict):   # {"type": "update", "move_path": …}
            c = {**c, **kind}
            kind = kind.get("type") or "update"
        out.append({"path": str(path or c.get("path") or ""), "kind": str(kind).lower(),
                    "diff": str(c.get("diff") or c.get("unified_diff") or c.get("content") or ""),
                    "move_path": c.get("movePath") or c.get("move_path")})
    return [c for c in out if c["path"]]


class CodexEngine(Engine):
    name = "codex"
    label = "Codex"
    capabilities = {
        "modes": list(MODES),
        "plan": False,       # no hay tarjeta de plan: «Solo planificar» = sin permiso de escritura
        "commands": False,
        "images": False,
        "cost": False,       # solo tokens
        "resume": False,     # «Retomar conversación» lista sesiones de Claude Code
    }
    models = CODEX_MODELS

    @classmethod
    def available(cls) -> bool:
        return available()

    def __init__(self, host: Host) -> None:
        super().__init__(host)
        self.proc: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task | None = None
        self.next_id = 0
        self.waiting: dict[int, asyncio.Future] = {}
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.turn_done: asyncio.Future | None = None
        self.usage: dict[str, Any] = {}
        self.stream_item: str | None = None
        self.commands_seen: dict[str, str] = {}
        self.changes_seen: dict[str, list[dict[str, Any]]] = {}
        self.dirty = False   # cambió el modo: al próximo turno se reabre con la política nueva
        self.last_error = ""

    @property
    def connected(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    # -- proceso y JSON-RPC ------------------------------------------------
    async def _start(self) -> None:
        if self.connected:
            return
        self.proc = await asyncio.create_subprocess_exec(
            *shlex.split(CODEX_CMD), cwd=self.host.cwd, env=codex_env(), limit=READ_LIMIT,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self.reader = asyncio.create_task(self._read())
        asyncio.create_task(self._drain_stderr(self.proc))
        await self._call("initialize", {"clientInfo": {"name": "mando", "title": "Mando", "version": __version__}})
        await self._send({"method": "initialized", "params": {}})

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        with contextlib.suppress(Exception):
            async for line in proc.stderr:
                log.debug("codex: %s", line.decode(errors="replace").rstrip())

    async def _send(self, msg: dict[str, Any]) -> None:
        if not self.connected:
            raise CodexError("Codex no está en ejecución")
        self.proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode())
        await self.proc.stdin.drain()

    async def _call(self, method: str, params: dict[str, Any], timeout: float = RPC_TIMEOUT) -> Any:
        self.next_id += 1
        rid = self.next_id
        fut = asyncio.get_running_loop().create_future()
        self.waiting[rid] = fut
        await self._send({"id": rid, "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.waiting.pop(rid, None)

    async def _read(self) -> None:
        proc = self.proc
        try:
            async for line in proc.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if "method" in msg and "id" in msg:      # petición de Codex (aprobaciones)
                    asyncio.create_task(self._on_request(msg))
                elif "method" in msg:                   # notificación
                    try:
                        self._on_notification(msg["method"], msg.get("params") or {})
                    except Exception:  # noqa: BLE001 — una notificación rara no corta la conversación
                        log.exception("Notificación de Codex no procesada: %s", msg.get("method"))
                elif "id" in msg:                       # respuesta a una llamada nuestra
                    fut = self.waiting.get(msg["id"])
                    if fut and not fut.done():
                        if "error" in msg:
                            err = msg["error"] or {}
                            fut.set_exception(CodexError(str(err.get("message") or err)))
                        else:
                            fut.set_result(msg.get("result"))
        finally:
            err = CodexError("Codex se cerró inesperadamente" + (f": {self.last_error}" if self.last_error else ""))
            for fut in list(self.waiting.values()):
                if not fut.done():
                    fut.set_exception(err)
            if self.turn_done and not self.turn_done.done():
                self.turn_done.set_exception(err)

    async def close(self) -> None:
        proc, self.proc = self.proc, None
        if self.reader:
            self.reader.cancel()
            self.reader = None
        if proc and proc.returncode is None:
            with contextlib.suppress(Exception):
                proc.stdin.close()
                proc.terminate()
                await asyncio.wait_for(proc.wait(), 5)
            if proc.returncode is None:
                with contextlib.suppress(Exception):
                    proc.kill()
        self.thread_id = None   # se retoma con thread/resume la próxima vez

    async def interrupt(self) -> None:
        if self.connected and self.thread_id and self.turn_id:
            with contextlib.suppress(Exception):
                await self._call("turn/interrupt", {"threadId": self.thread_id, "turnId": self.turn_id}, timeout=5)

    async def set_mode(self, mode: str) -> None:
        self.dirty = self.connected   # la política se fija al abrir el thread

    async def set_model(self, model: str) -> None:
        pass   # se envía en cada turn/start

    # -- un turno -------------------------------------------------------------
    def _thread_config(self) -> dict[str, Any]:
        policy, sandbox = MODES.get(self.host.mode, MODES["default"])
        cfg: dict[str, Any] = {"cwd": self.host.cwd,
                               "approvalPolicy": os.getenv("CODEX_APPROVAL_POLICY") or policy,
                               "sandbox": os.getenv("CODEX_SANDBOX") or sandbox}
        if self.host.model and self.host.model != "default":
            cfg["model"] = self.host.model
        return cfg

    async def _open_thread(self) -> None:
        if self.dirty:
            await self.close()
            self.dirty = False
        await self._start()
        if self.thread_id:
            return
        cfg = self._thread_config()
        res = None
        if self.host.session_id:   # retomar la conversación guardada de esta pestaña
            try:
                res = await self._call("thread/resume", {"threadId": self.host.session_id, **cfg})
            except CodexError as e:
                log.info("No se pudo retomar el thread de Codex (%s): se abre uno nuevo", e)
        if res is None:
            res = await self._call("thread/start", cfg)
        thread = (res or {}).get("thread") or {}
        self.thread_id = thread.get("id") or (res or {}).get("threadId")
        if not self.thread_id:
            raise CodexError("Codex no devolvió el id de la conversación")

    async def run(self, prompt: str, images: list[dict[str, Any]] | None = None) -> None:
        await self._open_thread()
        self.usage, self.last_error, self.stream_item = {}, "", None
        self.turn_done = asyncio.get_running_loop().create_future()
        params: dict[str, Any] = {"threadId": self.thread_id, "input": [{"type": "text", "text": prompt}]}
        if self.host.model and self.host.model != "default":
            params["model"] = self.host.model
        res = await self._call("turn/start", params)
        self.turn_id = ((res or {}).get("turn") or {}).get("id") or self.turn_id
        try:
            turn = await self.turn_done
        finally:
            self.turn_id = None
        if self.stream_item:
            self.host.stream_stop()
            self.stream_item = None
        status = str(turn.get("status") or "completed")
        error = (turn.get("error") or {}).get("message") or self.last_error
        u = self.usage
        self.host.turn_result(TurnResult(
            is_error=status == "failed", session_id=self.thread_id, cost=None,
            usage={"input_tokens": u.get("inputTokens", 0), "output_tokens": u.get("outputTokens", 0),
                   "cache_read_input_tokens": u.get("cachedInputTokens", 0)},
            model=self.host.model or "codex"))
        if status == "failed" and error:
            raise CodexError(error)

    # -- notificaciones -------------------------------------------------------
    def _on_notification(self, method: str, p: dict[str, Any]) -> None:
        if p.get("threadId") and self.thread_id and p["threadId"] != self.thread_id:
            return
        if method == "turn/started":
            self.turn_id = (p.get("turn") or {}).get("id") or self.turn_id
        elif method == "item/agentMessage/delta":
            item = p.get("itemId")
            if item != self.stream_item:
                if self.stream_item:
                    self.host.stream_stop()
                self.host.stream_start(item)
                self.stream_item = item
            self.host.stream_delta(str(p.get("delta") or ""))
        elif method in ("item/started", "item/completed"):
            self._on_item(method == "item/started", p.get("item") or {})
        elif method == "thread/tokenUsage/updated":
            tu = p.get("tokenUsage") or {}
            self.usage = tu.get("last") or tu.get("total") or self.usage
        elif method == "turn/completed":
            turn = p.get("turn") or {}
            if self.turn_done and not self.turn_done.done():
                self.turn_done.set_result(turn)
        elif method == "error":
            err = p.get("error") or p
            self.last_error = str(err.get("message") or "")[:500] if isinstance(err, dict) else str(err)[:500]

    def _on_item(self, started: bool, item: dict[str, Any]) -> None:
        kind, iid = item.get("type"), str(item.get("id") or "")
        if kind == "agentMessage" and not started:
            if self.stream_item == iid:
                self.host.stream_stop()
                self.stream_item = None
            self.host.final_text(str(item.get("text") or ""), iid)
        elif kind == "commandExecution" and started:
            cmd = command_text(item.get("command"))
            self.commands_seen[iid] = cmd
            self.host.tool_used("Bash", {"command": cmd})
        elif kind == "fileChange" and started:
            changes = normalize_changes(item.get("changes"))
            self.changes_seen[iid] = changes
            for c in changes:
                self.host.tool_used("ApplyPatch", {"file_path": c["path"]})
        elif kind == "mcpToolCall" and started:
            self.host.tool_used(f"mcp__{item.get('server', '')}__{item.get('tool', '')}",
                                item.get("arguments") if isinstance(item.get("arguments"), dict) else {})
        elif kind == "webSearch" and started:
            self.host.tool_used("WebSearch", {"query": str(item.get("query") or "")})

    # -- aprobaciones -----------------------------------------------------------
    async def _on_request(self, msg: dict[str, Any]) -> None:
        method, p, rid = msg["method"], msg.get("params") or {}, msg["id"]
        try:
            if method.endswith("commandExecution/requestApproval"):
                decision = await self._approve_command(p)
            elif method.endswith("fileChange/requestApproval"):
                decision = await self._approve_changes(p)
            else:   # preguntas u otras peticiones que Mando aún no muestra: se rechazan sin colgar a Codex
                await self._send({"id": rid, "error": {"code": -32601, "message": f"Mando no admite {method}"}})
                return
            await self._send({"id": rid, "result": {"decision": decision}})
        except Exception:  # noqa: BLE001
            log.exception("Error respondiendo a Codex (%s)", method)
            with contextlib.suppress(Exception):
                await self._send({"id": rid, "result": {"decision": "decline"}})

    @staticmethod
    def _answer(d: Decision) -> str:
        return "accept" if d.allow else ("cancel" if d.interrupt else "decline")

    async def _approve_command(self, p: dict[str, Any]) -> str:
        iid = str(p.get("itemId") or p.get("callId") or "")
        cmd = command_text(p.get("command")) or self.commands_seen.get(iid, "")
        if not cmd and p.get("parsedCmd"):
            cmd = " ".join(str(c.get("cmd", "")) for c in p["parsedCmd"] if isinstance(c, dict))
        data = {"command": cmd}
        if await self.host.pre_tool("Bash", data):   # bloqueado por una regla
            return "decline"
        return self._answer(await self.host.decide_tool("Bash", data))

    async def _approve_changes(self, p: dict[str, Any]) -> str:
        iid = str(p.get("itemId") or p.get("callId") or "")
        changes = normalize_changes(p.get("changes")) or self.changes_seen.get(iid, [])
        datas = [{"file_path": c["path"], "kind": c["kind"], "diff": c["diff"], "move_path": c["move_path"]}
                 for c in changes]
        for data in datas:   # primero las reglas de bloqueo (y se guarda el contenido original)
            if await self.host.pre_tool("ApplyPatch", data):
                return "decline"
        for data in datas:   # una tarjeta por archivo; si rechazas uno, no se aplica el lote
            d = await self.host.decide_tool("ApplyPatch", data)
            if not d.allow:
                return self._answer(d)
        return "accept"
