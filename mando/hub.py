"""Bus de eventos hacia los navegadores conectados, con historial persistente."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from .config import log

HISTORY_MAX = 400
SSE_QUEUE_MAX = 1000  # eventos en cola por navegador antes de soltarlo


# Eventos de estado que no se guardan en el historial (se reemplazan unos a otros)
EPHEMERAL_EVENTS = ("status", "stream", "stream_drop", "revoked", "reset")


# Historial del chat en disco (sobrevive a reinicios)
HISTORY_PERSIST = os.getenv("HISTORY_PERSIST", "1") not in ("0", "false", "no")


HISTORY_FILE = Path(os.getenv("HISTORY_FILE", "~/.mando-history.json")).expanduser()


HISTORY_SAVE_DELAY = 0.5


class Hub:
    """Difunde cada evento a todos los navegadores conectados (SSE o long-polling)."""

    def __init__(self) -> None:
        self.history: list[dict[str, Any]] = []
        self.clients: set[asyncio.Queue] = set()
        self.seq = 0
        self.trimmed_upto = 0  # id del último evento descartado del historial
        self._changed = asyncio.Event()
        self._save_pending = False
        self.listeners: list = []   # funciones que reciben cada evento del historial (p. ej. Web Push)

    def emit(self, type_: str, **data: Any) -> dict[str, Any]:
        self.seq += 1
        # Los datos nunca pisan el número de secuencia (los navegadores descartan ids ya vistos)
        ev = {**data, "id": self.seq, "type": type_, "ts": time.time()}
        if type_ not in EPHEMERAL_EVENTS:
            self.history.append(ev)
            self.trim(ev.get("tab"))
            self.schedule_save()
            for fn in self.listeners:
                try:
                    fn(ev)
                except Exception:  # noqa: BLE001 — un aviso que falla no detiene el chat
                    log.exception("Fallo en un oyente de eventos")
        for q in list(self.clients):
            if q.qsize() > SSE_QUEUE_MAX:
                # Cliente que no consume (conexión muerta): se le desconecta y, al
                # reconectar, recibe un snapshot completo.
                self.clients.discard(q)
                q.put_nowait(None)
            else:
                q.put_nowait(ev)
        # Despierta a los clientes en long-polling
        self._changed.set()
        self._changed = asyncio.Event()
        return ev

    def trim(self, tab: str | None) -> None:
        """Máximo HISTORY_MAX eventos por sesión: una sesión muy activa no borra lo de las demás."""
        same = [i for i, e in enumerate(self.history) if e.get("tab") == tab]
        extra = len(same) - HISTORY_MAX
        if extra > 0:
            drop = set(same[:extra])
            self.trimmed_upto = max(self.trimmed_upto, *(self.history[i]["id"] for i in drop))
            self.history = [e for i, e in enumerate(self.history) if i not in drop]

    def drop_tab(self, tab: str) -> None:
        """Al cerrar una sesión se borra su historial; los navegadores recargan."""
        self.history = [e for e in self.history if e.get("tab") != tab]
        self.trimmed_upto = self.seq
        self.save()
        self.emit("reset")

    # -- persistencia ----------------------------------------------------
    def schedule_save(self) -> None:
        """Guarda poco después del último cambio (agrupa ráfagas de eventos)."""
        if not HISTORY_PERSIST or self._save_pending:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.save()
            return
        self._save_pending = True
        loop.call_later(HISTORY_SAVE_DELAY, self.save)

    def save(self) -> None:
        self._save_pending = False
        if not HISTORY_PERSIST:
            return
        try:
            tmp = HISTORY_FILE.with_name(HISTORY_FILE.name + ".tmp")
            tmp.write_text(json.dumps({"seq": self.seq, "history": self.history}, ensure_ascii=False))
            tmp.chmod(0o600)
            tmp.replace(HISTORY_FILE)  # escritura atómica
        except OSError:
            log.exception("No se pudo guardar el historial")

    def load(self, default_tab: str | None = None) -> None:
        """Recupera el historial; las decisiones que quedaron sin responder se marcan expiradas.

        Los eventos de versiones sin sesiones paralelas se asignan a la primera sesión."""
        if not HISTORY_PERSIST or not HISTORY_FILE.exists():
            return
        try:
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.exception("No se pudo leer el historial; se empieza vacío")
            return
        self.history = [e for e in data.get("history", []) if isinstance(e, dict) and "id" in e]
        if default_tab:
            for e in self.history:
                e.setdefault("tab", default_tab)
        self.seq = max([int(data.get("seq", 0))] + [e["id"] for e in self.history])
        self.trimmed_upto = self.history[0]["id"] - 1 if self.history else self.seq
        for tab in {e.get("tab") for e in self.history}:
            self.trim(tab)
        done_q = {e.get("qid") for e in self.history if e.get("type") in ("unqueued", "user") and e.get("qid")}
        for e in [e for e in self.history if e.get("type") == "queued" and e.get("qid") not in done_q]:
            self.emit("unqueued", qid=e["qid"], label="⚠️ No se envió: el servidor se reinició",
                      **({"tab": e["tab"]} if e.get("tab") else {}))
        decided = {e.get("ref") for e in self.history if e.get("type") == "decided"}
        for e in [e for e in self.history if e.get("type") in ("perm", "question") and e.get("ref") not in decided]:
            self.emit("decided", ref=e["ref"], label="⚠️ Expirada: el servidor se reinició",
                      **({"tab": e["tab"]} if e.get("tab") else {}))
        log.info("Historial recuperado: %d eventos", len(self.history))

    def clear(self) -> None:
        self.history = []
        self.trimmed_upto = self.seq
        self.save()
        self.emit("reset")  # los navegadores recargan el historial (vacío)

    def since(self, after: int) -> list[dict[str, Any]] | None:
        """Eventos posteriores a `after`; None si ya no están en el historial (hace falta snapshot)."""
        if after < self.trimmed_upto or after > self.seq:
            return None
        return [e for e in self.history if e["id"] > after]

    async def wait(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._changed.wait(), timeout)
        except asyncio.TimeoutError:
            pass

    def snapshot(self, status: dict[str, Any], streams: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return {"type": "snapshot", "seq": self.seq, "history": self.history, "status": status,
                "streams": streams or []}


HUB = Hub()

