#!/usr/bin/env python3
"""Chrome Native Messaging host: start/status/stop send-plugin local queue (8767).

Message format: 4-byte little-endian length + UTF-8 JSON.
Commands: ping | status | start | stop
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HOST_DIR = Path(__file__).resolve().parent
ROOT = HOST_DIR.parent  # packages/send-plugin
QUEUE_PY = ROOT / "python" / "local_queue.py"
CFG = ROOT / "config" / "one-mailbox.json"
HEALTH = "http://127.0.0.1:8767/api/health"
LOG = ROOT / "data" / "logs" / "native-host.log"


def log(msg: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg + "\n")
    except Exception:
        pass


def read_msg():
    raw_len = sys.stdin.buffer.read(4)
    if not raw_len or len(raw_len) < 4:
        return None
    (length,) = struct.unpack("<I", raw_len)
    if length <= 0 or length > 1024 * 1024:
        return None
    data = sys.stdin.buffer.read(length)
    return json.loads(data.decode("utf-8"))


def write_msg(obj: dict) -> None:
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(data)))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def health() -> dict | None:
    try:
        with urllib.request.urlopen(HEALTH, timeout=2.5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _pick_pythonw() -> str:
    if os.name == "nt":
        exe = sys.executable
        if exe.lower().endswith("python.exe"):
            cand = exe[:-10] + "pythonw.exe"
            if os.path.isfile(cand):
                return cand
        local = os.path.expandvars(
            r"%LOCALAPPDATA%\\Microsoft\\WindowsApps\\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\\pythonw.exe"
        )
        if os.path.isfile(local):
            return local
    return sys.executable


def start_queue() -> dict:
    h = health()
    if h and h.get("ok"):
        return {"ok": True, "already": True, "running": True, "health": h}
    if not QUEUE_PY.is_file():
        return {"ok": False, "error": f"missing {QUEUE_PY}"}
    creation = 0
    if os.name == "nt":
        creation = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        stdout = (ROOT / "data" / "logs" / "queue-stdout.log").open("a", encoding="utf-8")
        stderr = (ROOT / "data" / "logs" / "queue-stderr.log").open("a", encoding="utf-8")
        subprocess.Popen(
            [_pick_pythonw(), str(QUEUE_PY), "--config", str(CFG)],
            cwd=str(ROOT),
            stdout=stdout,
            stderr=stderr,
            creationflags=creation if os.name == "nt" else 0,
            close_fds=True,
        )
    except Exception as e:
        log("start fail: " + str(e))
        return {"ok": False, "error": str(e)}
    for _ in range(40):  # ~10s
        time.sleep(0.25)
        h = health()
        if h and h.get("ok"):
            return {"ok": True, "started": True, "running": True, "health": h}
    return {"ok": False, "error": "queue started but health not ready", "running": False}


def stop_queue() -> dict:
    # Best-effort: call Stop-LocalQueue.ps1 if present
    ps1 = ROOT / "scripts" / "Stop-LocalQueue.ps1"
    if ps1.is_file() and os.name == "nt":
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
                capture_output=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as e:
            return {"ok": False, "error": str(e)}
    h = health()
    return {"ok": True, "running": bool(h and h.get("ok"))}


def main() -> int:
    log("host up root=" + str(ROOT))
    while True:
        try:
            msg = read_msg()
        except Exception as e:
            log("read err " + str(e))
            break
        if msg is None:
            break
        cmd = (msg.get("cmd") or msg.get("action") or "ping").strip().lower()
        log("cmd=" + cmd)
        try:
            if cmd == "ping":
                write_msg({"ok": True, "pong": True, "root": str(ROOT)})
            elif cmd == "status":
                h = health()
                write_msg({"ok": True, "running": bool(h and h.get("ok")), "health": h})
            elif cmd == "start" or cmd == "ensure":
                write_msg(start_queue())
            elif cmd == "stop":
                write_msg(stop_queue())
            else:
                write_msg({"ok": False, "error": "unknown cmd: " + cmd})
        except Exception as e:
            log("cmd err " + str(e))
            try:
                write_msg({"ok": False, "error": str(e)})
            except Exception:
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
