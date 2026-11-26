"""`codex app-server` simulado para las pruebas: habla JSON-RPC por stdio (una línea JSON por mensaje).

Variables:
  FAKE_CODEX       basic (por defecto) | lento | falla
  FAKE_CODEX_LOG   archivo donde se anotan las llamadas recibidas (para verificarlas en las pruebas)
"""

import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

SCENARIO = os.getenv("FAKE_CODEX", "basic")
LOG = os.getenv("FAKE_CODEX_LOG")
out_lock = threading.Lock()
answers: dict[int, queue.Queue] = {}
interrupted = threading.Event()
next_req = [1000]
threads: dict[str, dict] = {}


def send(msg):
    with out_lock:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()


def note(kind, data):
    if LOG:
        with open(LOG, "a") as f:
            f.write(json.dumps({"kind": kind, **data}) + "\n")


def ask(method, params):
    """Petición de Codex a Mando (aprobación): espera la respuesta."""
    next_req[0] += 1
    rid = next_req[0]
    answers[rid] = queue.Queue()
    send({"id": rid, "method": method, "params": params})
    msg = answers[rid].get(timeout=120)
    return (msg.get("result") or {}).get("decision") or ("error" if "error" in msg else None)


def notify(method, params):
    send({"method": method, "params": params})


def run_turn(thread_id, turn_id, text, cwd):
    tp = {"threadId": thread_id, "turnId": turn_id}
    notify("turn/started", {"threadId": thread_id, "turn": {"id": turn_id, "status": "inProgress", "items": []}})
    if SCENARIO == "falla":
        notify("error", {"threadId": thread_id, "error": {"message": "Not logged in: run codex login"}})
        notify("turn/completed", {"threadId": thread_id, "turn": {"id": turn_id, "status": "failed",
                                                                 "error": {"message": "Not logged in: run codex login"}}})
        return
    for part in ["Reviso ", "el proyecto ", "con Codex."]:
        notify("item/agentMessage/delta", {**tp, "itemId": "m1", "delta": part})
        time.sleep(0.15 if SCENARIO == "lento" else 0.05)
    notify("item/completed", {**tp, "item": {"type": "agentMessage", "id": "m1", "text": "Reviso el proyecto con Codex."}})
    if SCENARIO == "lento":
        for _ in range(200):
            if interrupted.is_set():
                notify("turn/completed", {"threadId": thread_id, "turn": {"id": turn_id, "status": "interrupted"}})
                return
            time.sleep(0.05)
    cmd = ["bash", "-lc", "npm test"]
    notify("item/started", {**tp, "item": {"type": "commandExecution", "id": "c1", "command": cmd, "cwd": cwd,
                                           "status": "inProgress"}})
    d_cmd = ask("item/commandExecution/requestApproval", {**tp, "itemId": "c1", "command": cmd, "cwd": cwd,
                                                          "reason": "ejecutar las pruebas"})
    notify("item/completed", {**tp, "item": {"type": "commandExecution", "id": "c1", "command": cmd,
                                             "status": "completed" if d_cmd == "accept" else "declined"}})
    changes = [{"path": "app.py", "kind": "update", "diff": "@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"},
               {"path": ".env", "kind": "update", "diff": "@@ -1 +1 @@\n-A=1\n+A=2\n"}] if "env" in text else \
              [{"path": "app.py", "kind": "update", "diff": "@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"}]
    notify("item/started", {**tp, "item": {"type": "fileChange", "id": "f1", "changes": changes, "status": "inProgress"}})
    d_files = ask("item/fileChange/requestApproval", {**tp, "itemId": "f1", "reason": "corregir f"})
    if d_files == "accept":
        p = Path(cwd) / "app.py"
        if p.exists():
            p.write_text(p.read_text().replace("return 1", "return 2"))
    notify("item/completed", {**tp, "item": {"type": "fileChange", "id": "f1", "changes": changes,
                                             "status": "completed" if d_files == "accept" else "declined"}})
    notify("thread/tokenUsage/updated", {**tp, "tokenUsage": {"last": {"inputTokens": 900, "outputTokens": 120,
                                                                       "cachedInputTokens": 300, "totalTokens": 1020}}})
    notify("item/agentMessage/delta", {**tp, "itemId": "m2", "delta": f"comando={d_cmd} archivos={d_files}"})
    notify("item/completed", {**tp, "item": {"type": "agentMessage", "id": "m2", "text": f"comando={d_cmd} archivos={d_files}"}})
    notify("turn/completed", {"threadId": thread_id, "turn": {"id": turn_id, "status": "completed"}})


def main():
    n_turn = 0
    for line in sys.stdin:
        msg = json.loads(line)
        method, rid, p = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method is None and rid in answers:     # respuesta de Mando a una aprobación
            answers[rid].put(msg)
            continue
        note("call", {"method": method, "params": p})
        if method == "initialize":
            send({"id": rid, "result": {"userAgent": "fake-codex/0.0"}})
        elif method == "initialized":
            pass
        elif method == "thread/start":
            tid = f"th-{len(threads) + 1}"
            threads[tid] = p
            send({"id": rid, "result": {"thread": {"id": tid}, "model": p.get("model") or "gpt-5-codex"}})
        elif method == "thread/resume":
            tid = p.get("threadId")
            if tid in threads or tid == "th-guardado":
                send({"id": rid, "result": {"thread": {"id": tid}}})
            else:
                send({"id": rid, "error": {"code": -32600, "message": "thread not found"}})
        elif method == "turn/start":
            n_turn += 1
            tid, turn_id = p["threadId"], f"tu-{n_turn}"
            interrupted.clear()
            send({"id": rid, "result": {"turn": {"id": turn_id, "status": "inProgress", "items": []}}})
            text = " ".join(i.get("text", "") for i in p.get("input") or [])
            cwd = (threads.get(tid) or {}).get("cwd") or os.getcwd()
            threading.Thread(target=run_turn, args=(tid, turn_id, text, cwd), daemon=True).start()
        elif method == "turn/interrupt":
            interrupted.set()
            send({"id": rid, "result": {}})
        elif rid is not None:
            send({"id": rid, "error": {"code": -32601, "message": f"no implementado: {method}"}})


if __name__ == "__main__":
    main()
