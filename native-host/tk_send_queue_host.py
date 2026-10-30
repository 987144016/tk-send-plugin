#!/usr/bin/env python3
"""Chrome Native Messaging host: start/status/stop send-plugin local queue (8767).

Message format: 4-byte little-endian length + UTF-8 JSON.
Commands: ping | status | start | stop | version | pull
On start/ensure: best-effort git pull --ff-only when ROOT is a git clone.
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
    """Ensure the local queue is running without waiting on GitHub."""
    pull_info = {"deferred_to_options": True}

    h = health()
    if h and h.get("ok"):
        return {"ok": True, "already": True, "running": True, "health": h, "pull": pull_info}
    if not QUEUE_PY.is_file():
        return {"ok": False, "error": f"missing {QUEUE_PY}", "pull": pull_info}
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
            creationflags=creation if creation else 0,
            close_fds=False,
        )
    except Exception as e:
        log("start fail: " + str(e))
        return {"ok": False, "error": str(e), "pull": pull_info}
    for _ in range(40):
        time.sleep(0.25)
        h = health()
        if h and h.get("ok"):
            return {"ok": True, "started": True, "running": True, "health": h, "pull": pull_info}
    return {"ok": False, "error": "queue started but health not ready", "running": False, "pull": pull_info}



def stop_queue() -> dict:
    # Best-effort: call Stop-LocalQueue.ps1 if present
    ps1 = ROOT / "scripts" / "Stop-LocalQueue.ps1"
    if ps1.is_file() and os.name == "nt":
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
                capture_output=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode != 0:
                return {"ok": False, "error": "queue_stop_failed"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    h = health()
    return {"ok": True, "running": bool(h and h.get("ok"))}


def restart_queue() -> dict:
    """Apply downloaded backend code only when no Gmail send is in progress."""
    state_file = ROOT / 'data' / 'queue-state.json'
    try:
        current = json.loads(state_file.read_text(encoding='utf-8')).get('current')
    except FileNotFoundError:
        current = None
    stopped = stop_queue()
    if not stopped.get('ok'):
        return stopped
    center_warning = None
    if (ROOT / 'config' / 'reply-relay-center.json').is_file():
        legacy_script = ROOT / 'scripts' / 'Stop-Legacy-Reply-Center.ps1'
        if legacy_script.is_file():
            legacy = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy',
                'Bypass', '-WindowStyle', 'Hidden', '-File', str(legacy_script)],
                capture_output=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if legacy.returncode != 0:
                center_warning = 'old_reply_center_did_not_stop'
    for _ in range(20):
        if not health():
            break
        time.sleep(0.25)
    if health():
        return {"ok": False, "error": "queue_did_not_stop"}
    started = start_queue()
    if current:
        started['current_preserved'] = True
    if center_warning:
        started['center_warning'] = center_warning
    return started



def read_local_version() -> str:
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _run_git_pull() -> dict:
    """Best-effort ff-only pull. Never raises."""
    try:
        r = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "git not found on PATH"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0:
        return {"ok": False, "error": "git pull failed", "output": out, "code": r.returncode}
    return {"ok": True, "method": "git_pull", "output": out, "local": read_local_version()}


def _run_team_update() -> dict:
    """Fetch newer public source for a ZIP install; keep local/private files."""
    script = ROOT / "scripts" / "Update-Team-OnStart.ps1"
    if not script.is_file():
        return {"ok": False, "error": "team_updater_missing"}
    before = read_local_version()
    try:
        proc = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden", "-File", str(script)], cwd=str(ROOT),
            capture_output=True, timeout=65,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}
    result_path = ROOT / "data" / "team-update-result.json"
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        result = {"ok": False, "error": "update_result_missing"}
    return {**result, "method": "team_update", "before": before,
            "after": result.get("after") or read_local_version()}


def pull_update() -> dict:
    """Manual/native update for both Git clones and private team ZIPs."""
    before = read_local_version()
    if not (ROOT / ".git").exists():
        result = _run_team_update()
    else:
        result = _run_git_pull()
    if not result.get("ok"):
        return result
    after = read_local_version()
    return {
        "ok": True,
        "updated": before != after,
        "method": result.get("method") or "git_pull",
        "before": before,
        "after": after,
        "output": result.get("output"),
        "reload_extension": True,
        "hint": "click Apply update and restart in Options to load new backend and extension code",
    }


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
                write_msg({"ok": True, "pong": True, "root": str(ROOT), "version": read_local_version()})
            elif cmd == "version":
                write_msg({"ok": True, "local": read_local_version(), "root": str(ROOT), "is_git": (ROOT / ".git").exists()})
            elif cmd == "status":
                h = health()
                write_msg({"ok": True, "running": bool(h and h.get("ok")), "health": h, "version": read_local_version()})
            elif cmd == "start" or cmd == "ensure":
                write_msg(start_queue())
            elif cmd == "stop":
                write_msg(stop_queue())
            elif cmd == "restart":
                write_msg(restart_queue())
            elif cmd in ("pull", "update", "apply_update", "check_update", "check-update"):
                write_msg(pull_update())
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
