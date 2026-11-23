"""Utilidades compartidas: levantar el servidor con Claude simulado y hablarle por HTTP."""

from __future__ import annotations

import http.cookiejar
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "tok-pruebas-0123456789abcdefghij"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def clean_env(tmp: Path, **extra: str) -> dict[str, str]:
    """Entorno aislado: sin tu .env, sin proxies y con estado en un directorio temporal."""
    env = {k: v for k, v in os.environ.items()
           if not k.upper().endswith("_PROXY") and k not in ("ACCESS_TOKEN", "MCP_SERVERS", "ATLASSIAN_MCP_URL")}
    env.update(CR_NO_DOTENV="1", ACCESS_TOKEN=TOKEN, STATE_FILE=str(tmp / "state.json"),
               CLAUDE_WORKDIR=str(tmp), ALLOWED_ROOTS=str(tmp), BIND_HOST="127.0.0.1",
               PYTHONUNBUFFERED="1", HOME=str(tmp))
    env.update(extra)
    return env


@dataclass
class Server:
    base: str
    proc: subprocess.Popen
    log: Path
    ssl_ctx: ssl.SSLContext | None = None
    jar: http.cookiejar.CookieJar = field(default_factory=http.cookiejar.CookieJar)

    def _opener(self, cookies: bool = True):
        handlers = [urllib.request.HTTPSHandler(context=self.ssl_ctx)] if self.ssl_ctx else []
        if cookies:
            handlers.append(urllib.request.HTTPCookieProcessor(self.jar))
        return urllib.request.build_opener(*handlers)

    def request(self, method: str, path: str, body: dict | None = None, headers: dict | None = None,
                cookies: bool = True, timeout: float = 35) -> tuple[int, dict, bytes]:
        data = json.dumps(body).encode() if body is not None else None
        h = {"Content-Type": "application/json", "X-CR": "1"} if body is not None else {}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with self._opener(cookies).open(req, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def clone(self) -> "Server":
        """Otro «dispositivo» contra el mismo servidor (cookies propias)."""
        return Server(base=self.base, proc=self.proc, log=self.log, ssl_ctx=self.ssl_ctx)

    def login(self) -> None:
        status, _, _ = self.request("GET", f"/?token={TOKEN}")
        assert status == 200, f"login falló ({status})"

    def api(self, path: str, body: dict) -> tuple[int, dict]:
        status, _, raw = self.request("POST", path, body)
        return status, json.loads(raw or b"{}")

    def poll(self, after: int, timeout: float = 25) -> dict:
        status, _, raw = self.request("GET", f"/api/poll?after={after}&wait={timeout:.1f}", timeout=timeout + 10)
        assert status == 200, status
        return json.loads(raw)

    # Eventos recibidos por long-polling: `events` guarda todos; `_pending` los aún no revisados
    events: list[dict] = field(default_factory=list)
    _pending: list[dict] = field(default_factory=list)
    _cursor: int = -1

    def wait_for(self, pred, timeout: float = 15) -> tuple[dict, list[dict]]:
        """Avanza por los eventos hasta que `pred(evento)` sea verdadero.
        Devuelve (evento, todos los eventos recibidos hasta ahora)."""
        deadline = time.time() + timeout
        while True:
            while self._pending:
                ev = self._pending.pop(0)
                if pred(ev):
                    return ev, list(self.events)
            if time.time() >= deadline:
                break
            batch = self.poll(self._cursor, timeout=min(5, max(1, deadline - time.time())))
            new = batch["history"] if batch["type"] == "snapshot" else batch["events"]
            if batch["type"] == "batch" and batch.get("status"):
                new = new + [{"type": "status", **batch["status"]}]  # el estado no va en el historial
            self._cursor = batch["seq"]
            self.events += new
            self._pending += new
        raise AssertionError(f"evento no llegó; recibidos: {[e['type'] for e in self.events]}\n"
                             f"{self.log.read_text()[-2000:]}")


def start_server(tmp: Path, scenario: str = "basic", **extra: str) -> Server:
    port = free_port()
    log = tmp / "server.log"
    env = clean_env(tmp, PORT=str(port), FAKE_SCENARIO=scenario, **extra)
    proc = subprocess.Popen([sys.executable, str(ROOT / "tests" / "fake_claude.py")], env=env, cwd=tmp,
                            stdout=log.open("w"), stderr=subprocess.STDOUT)
    scheme = "https" if extra.get("TLS_CERT") else "http"
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("el servidor terminó al arrancar:\n" + log.read_text())
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError("el servidor no abrió el puerto:\n" + log.read_text())
    return Server(base=f"{scheme}://127.0.0.1:{port}", proc=proc, log=log)


@pytest.fixture
def make_server(tmp_path):
    servers: list[Server] = []

    def _make(scenario: str = "basic", **extra: str) -> Server:
        s = start_server(tmp_path, scenario, **extra)
        servers.append(s)
        return s

    yield _make
    for s in servers:
        s.proc.terminate()
        try:
            s.proc.wait(5)
        except subprocess.TimeoutExpired:
            s.proc.kill()


@pytest.fixture
def srv(make_server) -> Server:
    """Servidor con el escenario básico y sesión iniciada."""
    s = make_server("basic")
    s.login()
    return s


def run_python(tmp: Path, code: str, **extra: str) -> subprocess.CompletedProcess:
    """Ejecuta código que importa server.py con un entorno propio (cada caso aislado)."""
    prelude = f"import sys, logging; sys.path.insert(0, {str(ROOT)!r}); logging.disable(logging.CRITICAL)\n"
    return subprocess.run([sys.executable, "-c", prelude + code], env=clean_env(tmp, PORT="1", **extra),
                          cwd=tmp, capture_output=True, text=True, timeout=30)


# ---------- navegador (Playwright) ----------
def _chrome_path() -> str | None:
    if os.getenv("CHROME_PATH"):
        return os.environ["CHROME_PATH"]
    return None  # usa el Chromium de Playwright (python -m playwright install chromium)


@pytest.fixture(scope="session")
def browser():
    pw_mod = pytest.importorskip("playwright.sync_api")
    with pw_mod.sync_playwright() as p:
        args = ["--no-sandbox", "--autoplay-policy=no-user-gesture-required",
                "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"]  # micrófono simulado
        try:
            if _chrome_path():
                br = p.chromium.launch(executable_path=_chrome_path(), args=args)
            else:
                # Chromium completo en headless nuevo: el «headless shell» por defecto no muestra
                # notificaciones del sistema, que las pruebas de alertas necesitan.
                br = p.chromium.launch(channel=os.getenv("PW_CHANNEL", "chromium"), args=args)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no hay navegador para Playwright: {e}")
        yield br
        br.close()


def has_openssl() -> bool:
    return shutil.which("openssl") is not None
