"""Estado persistente en disco: token, carpeta, sesión de Claude, modo, reglas y 2FA."""

from __future__ import annotations

import json
import os
import secrets
from typing import Any

from .config import STATE_FILE


def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:  # noqa: BLE001
        return {}


def save_state(data: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(data, indent=2))
    STATE_FILE.chmod(0o600)


STATE = load_state()


ACCESS_TOKEN = os.getenv("ACCESS_TOKEN") or STATE.get("token") or secrets.token_urlsafe(24)


STATE["token"] = ACCESS_TOKEN

