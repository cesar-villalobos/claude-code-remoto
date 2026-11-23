"""Arranca el servidor con un Claude Code simulado, para las pruebas.

No necesita Claude Code ni conexión: reemplaza ClaudeSDKClient por un cliente
falso que emite mensajes y pide decisiones de forma determinista.

Variables:
  FAKE_SCENARIO  basic (por defecto) | slow | stream | edit | plan
  FAKE_WHISPER   1 = simula Whisper instalado (transcripción falsa)
  FAKE_DELAY     multiplicador de las pausas del escenario slow (por defecto 1)
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claude_remoto import agent, app  # noqa: E402
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock  # noqa: E402
from claude_agent_sdk.types import PermissionResultDeny, StreamEvent  # noqa: E402

SCENARIO = os.getenv("FAKE_SCENARIO", "basic")
DELAY = float(os.getenv("FAKE_DELAY", "1"))


def text(t: str) -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(text=t)], model="fake")


def tool(name: str, data: dict) -> AssistantMessage:
    return AssistantMessage(content=[ToolUseBlock(id=name, name=name, input=data)], model="fake")


def result(session_id: str) -> ResultMessage:
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                         num_turns=1, session_id=session_id, total_cost_usd=0.0012)


class FakeClient:
    def __init__(self, options):
        self.o = options

    async def connect(self):
        print(f"FAKE connect permission_mode={self.o.permission_mode}", flush=True)

    async def get_server_info(self):
        print("FAKE get_server_info", flush=True)
        return {"commands": [
            {"name": "context", "description": "Muestra el uso del contexto", "argumentHint": ""},
            {"name": "compact", "description": "Compacta la conversación", "argumentHint": "<instrucciones>"},
            {"name": "revisar-pr", "description": "Revisa un pull request (project)", "argumentHint": "<número>"},
            {"name": "atlassian:buscar-jira", "description": "Busca issues en Jira (MCP)", "argumentHint": "<jql>"},
            "cost", {"name": "nombre con espacios"}, {"description": "sin nombre"},
        ]}

    async def set_permission_mode(self, mode):
        print(f"FAKE set_permission_mode {mode}", flush=True)

    async def disconnect(self):
        pass

    async def interrupt(self):
        pass

    async def query(self, prompt):
        self.images = []
        if not isinstance(prompt, str):  # mensaje con bloques (capturas adjuntas)
            async for msg in prompt:
                blocks = msg["message"]["content"]
                self.images = [b["source"]["media_type"] for b in blocks if b["type"] == "image"]
                prompt = next(b["text"] for b in blocks if b["type"] == "text")
            print(f"FAKE imagenes {len(self.images)}", flush=True)
        self.prompt = prompt

    async def use_tool(self, tool, data):
        """Como el CLI: primero los hooks PreToolUse (pueden bloquear) y luego el permiso."""
        for matcher in (self.o.hooks or {}).get("PreToolUse", []):
            for hook in matcher.hooks:
                out = await hook({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": data,
                                  "tool_use_id": "t1", "session_id": "s", "transcript_path": "", "cwd": self.o.cwd},
                                 "t1", {"signal": None})
                spec = (out or {}).get("hookSpecificOutput") or {}
                if spec.get("permissionDecision") == "deny":
                    return PermissionResultDeny(message=spec.get("permissionDecisionReason", ""))
        return await self.o.can_use_tool(tool, data, None)

    async def receive_response(self):
        if getattr(self, "images", None):
            yield text(f"Recibí {len(self.images)} imagen(es): {', '.join(self.images)} · «{self.prompt}»")
            yield result("66666666-6666-6666-6666-666666666666")
            return
        if SCENARIO == "plan":
            plan = "## Plan\n1. Revisar `auth/login.py`\n2. Agregar validación del token\n3. Escribir pruebas"
            r = await self.o.can_use_tool("ExitPlanMode", {"plan": plan}, None)
            msg = getattr(r, "message", "")
            yield text(f"Plan: {type(r).__name__}" + (f" | {msg}" if msg else ""))
            yield result("55555555-5555-5555-5555-555555555555")
            return
        if SCENARIO == "edit":
            # Edita un archivo real de la carpeta de trabajo (para el diff) y crea uno nuevo grande
            app = Path(self.o.cwd) / "app.py"
            app.write_text("".join(f"linea_{i} = {i}\n" for i in range(1, 61)) + "def total():\n    return 42\n")
            r1 = await self.o.can_use_tool("Edit", {"file_path": str(app), "old_string": "    return 42",
                                                    "new_string": "    # suma corregida\n    return 43"}, None)
            yield text(f"Edit: {type(r1).__name__}")
            big = "".join(f"print('fila {i}')\n" for i in range(1, 81))
            r2 = await self.o.can_use_tool("Write", {"file_path": str(Path(self.o.cwd) / "nuevo.py"),
                                                     "content": big}, None)
            yield text(f"Write: {type(r2).__name__}")
            yield result("44444444-4444-4444-4444-444444444444")
            return
        if SCENARIO == "stream":
            # Respuesta escrita por partes, como la API real con include_partial_messages
            def ev(e):
                return StreamEvent(uuid="u", session_id="s", event=e)
            chunks = ["Revisé el ", "módulo de **login** ", "y encontré ", "el problema:\n\n", "faltaba validar ", "el token."]
            yield ev({"type": "message_start", "message": {"id": "msg_1"}})
            yield ev({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
            for c in chunks:
                await asyncio.sleep(0.5 * DELAY)
                yield ev({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": c}})
            yield ev({"type": "content_block_stop", "index": 0})
            yield AssistantMessage(content=[TextBlock(text="".join(chunks))], model="fake", message_id="msg_1")
            yield result("33333333-3333-3333-3333-333333333333")
            return
        if SCENARIO == "slow":
            # Para probar el indicador de trabajo: herramientas con pausas y un permiso de edición
            await asyncio.sleep(1.5 * DELAY)
            yield tool("Read", {"file_path": "src/auth/login.py"})
            await asyncio.sleep(3 * DELAY)
            yield tool("Grep", {"pattern": "validate_token"})
            await asyncio.sleep(2 * DELAY)
            r = await self.o.can_use_tool("Edit", {"file_path": "src/auth/login.py", "new_string": "return ok"}, None)
            await asyncio.sleep(2 * DELAY)
            yield text(f"Listo ({type(r).__name__}).")
            yield result("22222222-2222-2222-2222-222222222222")
            return

        # basic: texto → permiso Bash → pregunta → resultado
        yield text("Voy a revisar **ABC-123**.\n```py\nprint(1)\n```")
        r = await self.use_tool("Bash", {"command": os.getenv("FAKE_COMMAND", "pytest -q")})
        msg = getattr(r, "message", "")
        yield text(f"Permiso: {type(r).__name__}" + (f" | {msg}" if msg else ""))
        r = await self.o.can_use_tool("AskUserQuestion", {"questions": [{
            "question": "¿Qué rama?", "header": "Rama", "multiSelect": False,
            "options": [{"label": "main", "description": "principal"}, {"label": "dev", "description": ""}],
        }]}, None)
        answers = (getattr(r, "updated_input", None) or {}).get("answers")
        yield text(f"Respuesta: {answers}")
        yield result("11111111-2222-3333-4444-555555555555")


agent.ClaudeSDKClient = FakeClient

if os.getenv("FAKE_WHISPER") == "0":  # como si faster-whisper no estuviera (aunque lo tengas instalado)
    from claude_remoto import voice  # noqa: E402

    voice.whisper_installed = lambda: False
elif os.getenv("FAKE_WHISPER"):  # Whisper simulado: devuelve el tamaño y el formato del audio recibido
    from claude_remoto import voice  # noqa: E402

    voice.whisper_installed = lambda: True
    voice.transcribe_file = lambda path: f"texto dictado {Path(path).suffix} {Path(path).stat().st_size} bytes"

if __name__ == "__main__":
    app.run([])
