"""Sesiones de los dispositivos conectados (una por navegador, revocables)."""

from __future__ import annotations

import hashlib
import secrets
import time
from typing import Any

from .config import SESSION_DAYS
from .hub import HUB
from .state import STATE, save_state


def ua_label(ua: str) -> str:
    """«iPhone · Safari», «Mac · Chrome», etc. a partir del User-Agent."""
    device = next((name for key, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                                          ("Macintosh", "Mac"), ("Windows", "Windows"), ("Linux", "Linux"))
                   if key in ua), "Dispositivo")
    browser = next((name for key, name in (("Edg/", "Edge"), ("Firefox/", "Firefox"), ("CriOS", "Chrome"),
                                           ("Chrome/", "Chrome"), ("Safari/", "Safari")) if key in ua), "")
    if not ua:
        return "Cliente sin navegador"
    return f"{device} · {browser}" if browser else device


class Sessions:
    """Una sesión por dispositivo. La cookie guarda un identificador aleatorio; aquí solo su hash."""

    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = STATE.setdefault("sessions", {})
        self._last_saved: dict[str, float] = {}

    @staticmethod
    def _hash(raw: str) -> str:
        return hashlib.sha256(raw.encode()).hexdigest()

    def create(self, ip: str, ua: str) -> tuple[str, dict[str, Any]]:
        raw = secrets.token_urlsafe(32)
        h = self._hash(raw)
        now = time.time()
        info = {"id": h[:10], "created": now, "last_seen": now, "ip": ip, "ua": ua[:300], "label": ua_label(ua)}
        self.data[h] = info
        self.prune()
        save_state(STATE)
        return raw, info

    def get(self, raw: str | None, ip: str = "") -> dict[str, Any] | None:
        if not raw:
            return None
        h = self._hash(raw)
        info = self.data.get(h)
        if info is None:
            return None
        if time.time() - info["created"] > SESSION_DAYS * 86400:
            self.data.pop(h, None)
            save_state(STATE)
            return None
        info["last_seen"] = time.time()
        if ip:
            info["ip"] = ip
        if time.time() - self._last_saved.get(h, 0) > 300:  # no escribir el disco en cada petición
            self._last_saved[h] = time.time()
            save_state(STATE)
        return info

    def revoke(self, public_id: str) -> dict[str, Any] | None:
        for h, info in list(self.data.items()):
            if info["id"] == public_id:
                del self.data[h]
                from .push import (
                    unsubscribe,  # el dispositivo cerrado deja de recibir avisos
                )
                unsubscribe(public_id)
                save_state(STATE)
                HUB.emit("revoked", session=public_id)  # el dispositivo afectado se desconecta al instante
                return info
        return None

    def revoke_all_except(self, public_id: str | None) -> int:
        victims = [info["id"] for info in self.data.values() if info["id"] != public_id]
        for pid in victims:
            self.revoke(pid)
        return len(victims)

    def prune(self) -> None:
        limit = time.time() - SESSION_DAYS * 86400
        for h in [h for h, i in self.data.items() if i["created"] < limit]:
            del self.data[h]

    def listing(self, current: str | None) -> list[dict[str, Any]]:
        items = sorted(self.data.values(), key=lambda i: i["last_seen"], reverse=True)
        return [{k: i[k] for k in ("id", "label", "ip", "created", "last_seen")} | {"current": i["id"] == current}
                for i in items]


SESSIONS = Sessions()

