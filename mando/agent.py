"""Pestañas (sesiones): cola, tarjetas de decisión, reglas, burbujas en vivo, resumen de cambios y costo.

El agente de programación de cada pestaña está detrás de un motor (mando/engines): esta capa no
sabe si es Claude Code u otro.
"""

from __future__ import annotations

import asyncio
import copy
import os
import secrets
import time
from pathlib import Path
from typing import Any

from . import __version__, budget, changes, rule_suggest, usage
from .attachments import files_meta, with_files
from .audit import REQUEST, audit
from .config import (
    AUTO_APPROVE_PREFIXES,
    CA_CERT,
    CHANGES_SUMMARY,
    DEFAULT_MODE,
    DEFAULT_WORKDIR,
    MODEL,
    MODEL_LABELS,
    PERMISSION_MODES,
    STREAM_INTERVAL,
    TLS_CERT,
    log,
)
from .decisions import CANCELLED, PENDING, Pending, tool_trace, tool_view, wait_decision
from .diff import edit_diff
from .engines import (
    DEFAULT_ENGINE,
    Decision,
    TurnResult,
    available_engines,
    engine_class,
)
from .engines.claude import (  # noqa: F401 — se usan desde otros módulos y pruebas
    gateway_host,
    normalize_commands,
)
from .hub import HUB
from .jira import config as jira_config
from .rules import DEFAULT_RULES, find_rule, rule_label, suggest_rule
from .state import STATE, save_state
from .templates import version as tpl_version
from .updates import update_info
from .voice import engine as voice_engine


def model_label(value: str) -> str:
    return MODEL_LABELS.get(value, value)


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
        # último total acumulado informado por sesión, para registrar solo lo gastado en cada turno
        self.cost_seen: dict[str, float] = {str(k): float(v) for k, v in (tab.get("cost_seen") or {}).items()
                                            if isinstance(v, (int, float))}
        self.closed = False
        self.finishing = False
        self.stopping = False   # se pidió detener la tarea en curso
        self.queue: list[dict[str, Any]] = []   # mensajes enviados mientras trabajaba
        self.tracker: changes.Tracker | None = None   # archivos que cambia la tarea en curso
        self.commands_cache: list[dict[str, str]] | None = None  # comandos «/» de esta conexión
        self.lock = asyncio.Lock()
        self.task: asyncio.Task | None = None
        self.always_allow: set[str] = set()
        self.rules: list[dict[str, Any]] = shared_rules()
        self.engine = engine_class(tab.get("engine"))(self)   # el agente de esta pestaña
        saved_model = tab.get("model") or ""
        self.model: str = saved_model if saved_model in self.engine.models else ""  # "" = predeterminado
        saved_mode = tab.get("mode")
        modes = self.engine.capabilities.get("modes") or list(PERMISSION_MODES)
        self.mode: str = saved_mode if saved_mode in modes else (DEFAULT_MODE if DEFAULT_MODE in modes else modes[0])
        self.started_at: float | None = None  # para el cronómetro de la animación
        # Texto parcial en curso y bloques terminados que esperan su mensaje definitivo
        self.stream: dict[str, Any] | None = None
        self.finished_streams: list[dict[str, Any]] = []
        self.final_texts: set[str] = set()   # respuestas definitivas de la tarea en curso
        self.last_stream_emit = 0.0

    def emit(self, type_: str, **data: Any) -> dict[str, Any] | None:
        if self.closed:  # sesión cerrada: lo que quede de su tarea no vuelve al historial
            return None
        ev = HUB.emit(type_, tab=self.id, **data)
        if type_ in ("perm", "question"):
            emit_status()   # el estado de las pestañas trae las decisiones pendientes al día
        return ev

    # -- respuestas en tiempo real -----------------------------------------
    def current_stream(self) -> dict[str, Any] | None:
        return ({"tab": self.id, "sid": self.stream["sid"], "text": self.stream["text"]}
                if self.stream and self.stream["text"] else None)

    def emit_stream(self) -> None:
        if self.stream and self.stream["text"]:
            self.last_stream_emit = time.monotonic()
            self.emit("stream", sid=self.stream["sid"], text=self.stream["text"])

    def stream_start(self, message_id: str | None) -> None:
        """El motor empieza a escribir un bloque de texto (se ve en vivo en todos los dispositivos)."""
        self.stream = {"sid": secrets.token_hex(4), "text": "", "msg": message_id}

    def stream_delta(self, text: str) -> None:
        if self.stream is None:
            return
        self.stream["text"] += text
        if time.monotonic() - self.last_stream_emit >= STREAM_INTERVAL:
            self.emit_stream()

    def stream_stop(self) -> None:
        if self.stream is None:
            return
        self.emit_stream()  # último fragmento
        if self.stream["text"]:
            self.finished_streams.append(self.stream)
        self.stream = None

    def final_text(self, text: str, message_id: str | None = None, subagent: bool = False) -> None:
        """Texto definitivo de una respuesta: reemplaza a su burbuja en vivo, si la hay."""
        if not text.strip():
            return
        st = None if subagent else self.take_stream(message_id)
        self.emit("assistant", text=text, **({"replaces": st["sid"]} if st else {}))
        if not subagent:
            self.final_texts.add(text.strip())

    def tool_used(self, tool_name: str, data: dict[str, Any]) -> None:
        self.emit("tool", text=tool_trace(tool_name, data))
        audit("tool", tool=tool_name, target=tool_trace(tool_name, data))

    def turn_result(self, r: TurnResult) -> None:
        """Fin de un turno: sesión para retomar, costo y uso, aviso del límite de gasto."""
        if r.session_id:
            self.session_id = r.session_id
            self.persist()
        cost = self.turn_cost(r)
        before = budget.info()
        rec = usage.record(tab=self.name, tab_id=self.id, cost=cost, usage=r.usage, duration=self.elapsed(),
                           turns=r.turns, model=r.model or self.model or MODEL or "default", ok=not r.is_error)
        self.emit("done", ok=not r.is_error, cost=cost, tokens=rec["in"] + rec["out"],
                  duration=self.elapsed())  # para avisar al terminar tareas largas
        notice = budget.crossed(before, budget.info())
        if notice:
            HUB.emit("info", text=notice)   # en todas las pestañas
            emit_status()

    def turn_cost(self, r: TurnResult) -> float | None:
        """Lo gastado en este turno. Si el motor informa el total acumulado de la sesión, se resta
        el último total visto de esa sesión; si bajó (/clear, o una sesión retomada sin total
        guardado), el total informado es todo lo nuevo."""
        if r.cost is None or not r.cost_cumulative:
            return r.cost
        key = r.session_id or self.session_id or ""
        total = float(r.cost)
        last = self.cost_seen.get(key, 0.0)
        self.cost_seen[key] = total
        while len(self.cost_seen) > 20:   # solo las sesiones recientes
            self.cost_seen.pop(next(iter(self.cost_seen)))
        self.persist()
        return round(total - last, 6) if total >= last else total

    def take_stream(self, message_id: str | None) -> dict[str, Any] | None:
        """Bloque parcial que corresponde al mensaje definitivo (mismo message_id, en orden)."""
        for i, st in enumerate(self.finished_streams):
            if message_id is None or st["msg"] is None or st["msg"] == message_id:
                return self.finished_streams.pop(i)
        # A veces el CLI entrega el mensaje definitivo antes del content_block_stop: el bloque
        # que se está escribiendo es el que corresponde (si no, quedaría duplicado al terminar)
        st = self.stream
        if st and st["text"] and (message_id is None or st["msg"] is None or st["msg"] == message_id):
            self.stream = None
            return st
        return None

    def close_streams(self) -> None:
        """Al terminar o interrumpir: lo ya escrito queda como mensaje definitivo."""
        pending = self.finished_streams + ([self.stream] if self.stream and self.stream["text"] else [])
        for st in pending:
            if st["text"].strip() in self.final_texts:   # ya llegó como respuesta definitiva: solo se quita la burbuja
                self.emit("stream_drop", sid=st["sid"])
            else:
                self.emit("assistant", text=st["text"], replaces=st["sid"])
        self.stream, self.finished_streams = None, []
        self.final_texts = set()

    def elapsed(self) -> float:
        """Segundos desde que empezó la tarea actual."""
        return round(time.time() - self.started_at, 1) if self.started_at else 0.0

    @property
    def busy(self) -> bool:
        # «finishing»: el estado final se publica desde dentro de la tarea, que aún no figura terminada
        return bool(self.task and not self.task.done()) and not self.finishing

    def tab_state(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "cwd": self.cwd, "session_id": self.session_id, "mode": self.mode,
                "model": self.model, "engine": self.engine.name, "cost_seen": self.cost_seen}

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
            "pending_refs": [k for k, p in PENDING.items() if p.tab == self.id],   # la interfaz cierra solo las que ya no están
            "always": sorted(self.always_allow),
            "mode": self.mode,
            "model": self.model or "default",
            "since": self.started_at,  # inicio de la tarea actual (epoch del servidor)
            "queued": len(self.queue),
            "engine": self.engine.name,
        }

    # -- permisos ---------------------------------------------------------
    async def decide_tool(self, tool_name: str, data: dict[str, Any]) -> Decision:
        """El motor pide usar una herramienta: reglas, aprobación siempre, o tarjeta para decidir."""
        if self.stopping:   # se pidió detener: lo que el agente alcance a pedir ya no muestra tarjetas
            return Decision(False, "El usuario detuvo la tarea.", interrupt=True)
        if tool_name == "AskUserQuestion":
            return await self.ask_questions(data)
        if tool_name in self.always_allow or any(tool_name.startswith(p) for p in AUTO_APPROVE_PREFIXES):
            return Decision(True)

        if tool_name == "ExitPlanMode":
            return await self.review_plan(data)

        # Reglas: bloquear (también lo hace el hook) y aprobar siempre
        rule = find_rule(self.rules, "deny", tool_name, data, self.cwd)
        if rule:
            audit("blocked", tool=tool_name, target=tool_trace(tool_name, data), rule=rule_label(rule))
            return Decision(False, self.deny_message(rule))
        rule = find_rule(self.rules, "allow", tool_name, data, self.cwd)
        if rule:
            self.emit("info", text=f"⚖️ Aprobado por regla: {tool_trace(tool_name, data)} · {rule_label(rule)}")
            audit("auto_approved", tool=tool_name, target=tool_trace(tool_name, data), rule=rule_label(rule))
            return Decision(True)

        pid = secrets.token_hex(6)
        PENDING[pid] = Pending(future=asyncio.get_running_loop().create_future(), kind="perm", tool_name=tool_name,
                               summary=tool_trace(tool_name, data), tab=self.id)
        title, detail = tool_view(tool_name, data)
        extra: dict[str, Any] = {}
        if tool_name in ("Edit", "MultiEdit", "Write", "ApplyPatch"):
            try:
                diff = edit_diff(tool_name, data, self.cwd)
            except Exception:  # noqa: BLE001 — el diff es una ayuda visual, nunca debe bloquear
                log.exception("No se pudo calcular el diff")
                diff = None
            if diff:
                extra["diff"] = diff
                if diff["new_file"] and tool_name != "ApplyPatch":
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
            return Decision(False, "El usuario no respondió a tiempo.")
        choice, comment = (decision.get("choice"), decision.get("comment", "")) if isinstance(decision, dict) \
            else (decision, "")
        if choice == "a":
            self.always_allow.add(tool_name)
        if choice == "y":
            offer = rule_suggest.on_approved(tool_name, data, self.cwd, self.rules)
            if offer:   # lo aprobaste varias veces: ¿permitir siempre?
                self.emit("rule_suggest", **offer)
        if choice in ("y", "a"):
            return Decision(True)
        if comment:
            # Claude recibe el motivo y puede corregir el rumbo en vez de detenerse
            return Decision(False, (
                f"El usuario rechazó esta acción y comentó: «{comment}». "
                "Ten en cuenta su comentario y propón otra forma de continuar."))
        return Decision(False, "El usuario rechazó esta acción.")

    def deny_message(self, rule: dict[str, Any]) -> str:
        return (f"Bloqueado por una regla del usuario ({rule_label(rule)}"
                + (f": {rule['note']}" if rule.get("note") else "") + "). "
                "No intentes lograr lo mismo de otra forma; si es imprescindible, explícale al usuario por qué.")

    async def pre_tool(self, tool_name: str, data: dict[str, Any]) -> str | None:
        """Antes de cada herramienta, en cualquier modo de permisos: el motivo si una regla la bloquea."""
        rule = find_rule(self.rules, "deny", tool_name, data, self.cwd)
        if not rule:
            if self.tracker:   # contenido original, para el resumen de cambios al terminar
                await asyncio.to_thread(self.tracker.before_tool, tool_name, data)
            return None
        self.emit("info", text=f"⛔ Bloqueado por regla: {tool_trace(tool_name, data)} · {rule_label(rule)}")
        audit("blocked", tool=tool_name, target=tool_trace(tool_name, data), rule=rule_label(rule))
        return self.deny_message(rule)

    async def review_plan(self, data: dict[str, Any]) -> Decision:
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
            return Decision(False, "El usuario no respondió a tiempo.")
        choice, comment = (decision.get("choice"), decision.get("comment", "")) if isinstance(decision, dict) \
            else (decision, "")
        if choice in ("y", "e"):
            # Al aprobar, Claude sale del modo plan. El cambio de modo se agenda aparte: no se puede
            # esperar una respuesta del CLI mientras este espera la nuestra.
            new_mode = "acceptEdits" if choice == "e" else "default"
            asyncio.get_running_loop().call_later(0.2, lambda: asyncio.create_task(self.set_mode(new_mode)))
            return Decision(True)
        if comment:
            return Decision(False, (
                f"El usuario no aprobó el plan y pidió cambios: «{comment}». "
                "Ajusta el plan según su comentario y vuelve a presentarlo."))
        return Decision(False, "El usuario no aprobó el plan. Sigue en modo plan.")

    async def ask_questions(self, data: dict[str, Any]) -> Decision:
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
                return Decision(False, "El usuario no respondió la pregunta a tiempo.")
            if answer == CANCELLED:
                return Decision(False, "El usuario canceló la tarea.")
            answers[q.get("question", "")] = answer
        return Decision(True, updated_input={"questions": data.get("questions", []), "answers": answers})

    # -- ciclo de vida ----------------------------------------------------
    def options(self):
        """Opciones del motor (solo Claude Code; se usan en las pruebas)."""
        return self.engine.options()

    async def commands(self) -> list[dict[str, str]]:
        """Comandos «/» que ofrece el agente (se guardan mientras dure la conexión)."""
        if self.commands_cache is None:
            self.commands_cache = await self.engine.commands()
        return self.commands_cache

    async def close(self) -> None:
        self.commands_cache = None  # otra carpeta o sesión puede tener otros comandos
        await self.engine.close()

    async def stop(self) -> None:
        self.stopping = True   # hasta que empiece la próxima tarea
        self.clear_queue("⏹️ Quitado: se detuvo la tarea")   # detener = retomar el control
        await self.engine.interrupt()
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
        try:
            await self.engine.set_mode(mode)
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
        try:
            await self.engine.set_model(self.model)
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

    def submit(self, prompt: str, images: list[dict[str, Any]] | None = None,
               files: list[dict[str, Any]] | None = None) -> str:
        """«started», «queued» (Claude está trabajando: va a la cola), «full» (cola llena)
        o «limit» (se alcanzó el límite de gasto mensual)."""
        images, files = images or [], files or []
        if budget.blocked():
            return "limit"
        thumbs = [i["thumb"] for i in images]
        if self.busy:
            if len(self.queue) >= MAX_QUEUE:
                return "full"
            qid = secrets.token_hex(4)
            self.queue.append({"qid": qid, "text": prompt, "images": images, "files": files})
            self.emit("queued", qid=qid, text=prompt, **({"images": thumbs} if images else {}),
                      **({"files": files_meta(files)} if files else {}))
            emit_status()
            return "queued"
        self.start(prompt, images, files=files)
        return "started"

    def start(self, prompt: str, images: list[dict[str, Any]], qid: str | None = None,
              files: list[dict[str, Any]] | None = None) -> None:
        thumbs = [i["thumb"] for i in images]
        files = files or []
        # en el historial: solo miniaturas y el nombre de los archivos; «qid» indica que salió de la cola
        self.emit("user", text=prompt, **({"images": thumbs} if images else {}),
                  **({"files": files_meta(files)} if files else {}), **({"qid": qid} if qid else {}))
        self.finishing = False
        self.stopping = False
        # el contenido de los archivos va solo a Claude (no queda en el historial ni en la auditoría)
        self.task = asyncio.create_task(self.run(with_files(prompt, files), images))

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
        self.start(item["text"], item["images"], qid=item["qid"], files=item.get("files"))

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
                await self.engine.run(prompt, images)
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

    def create(self, name: str, cwd: str | None = None, engine: str | None = None) -> Agent:
        agent = Agent({"id": secrets.token_hex(4), "name": name, "cwd": cwd or self.first().cwd,
                       "mode": self.first().mode, "engine": engine or DEFAULT_ENGINE})
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
        "models": [{"value": m, "label": model_label(m)} for m in agent.engine.models],   # los del motor de la pestaña
        "engines": [{**e, "models": [{"value": m, "label": model_label(m)} for m in e["models"]]}
                    for e in available_engines()],   # Claude Code y, si está instalado, Codex
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

