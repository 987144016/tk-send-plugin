#!/usr/bin/env python3
"""Chrome Native Messaging host: start/status/stop send-plugin local queue (8767).

Message format: 4-byte little-endian length + UTF-8 JSON.
Commands: ping | status | start | stop | version | check_update | apply_update
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



REPO_SLUG = "987144016/tk-send-plugin"
REMOTE_VERSION_URL = f"https://raw.githubusercontent.com/{REPO_SLUG}/main/VERSION"
ZIP_URL = f"https://github.com/{REPO_SLUG}/archive/refs/heads/main.zip"
RELEASES_API = f"https://api.github.com/repos/{REPO_SLUG}/releases/latest"


def read_local_version() -> str:
    p = ROOT / "VERSION"
    try:
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def _http_get(url: str, timeout: float = 15.0) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "tk-send-plugin-updater",
            "Accept": "application/vnd.github+json, text/plain, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_remote_version() -> dict:
    """Prefer raw VERSION on main; fall back to latest release tag."""
    err = None
    try:
        raw = _http_get(REMOTE_VERSION_URL, timeout=12).decode("utf-8", errors="replace").strip()
        if raw:
            return {"ok": True, "remote": raw, "source": "branch_VERSION"}
    except Exception as e:
        err = str(e)
    try:
        data = json.loads(_http_get(RELEASES_API, timeout=12).decode("utf-8"))
        tag = (data.get("tag_name") or "").strip()
        if tag:
            return {"ok": True, "remote": tag.lstrip("vV"), "source": "release_tag", "tag": tag}
    except Exception as e:
        err = (err + "; " if err else "") + str(e)
    return {"ok": False, "error": err or "remote version unavailable"}


def _ver_tuple(s: str):
    s = (s or "").strip().lstrip("vV")
    core, _, suffix = s.partition("-")
    parts = []
    for bit in core.split("."):
        try:
            parts.append(int(bit))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return (tuple(parts[:4]), suffix)


def version_is_newer(remote: str, local: str) -> bool:
    if not remote:
        return False
    if not local:
        return True
    rt, rs = _ver_tuple(remote)
    lt, ls = _ver_tuple(local)
    if rt != lt:
        return rt > lt
    # Same numeric core: not newer (avoids bounce-label vs bare thrash)
    return False


def check_update() -> dict:
    local = read_local_version()
    rem = fetch_remote_version()
    if not rem.get("ok"):
        return {"ok": False, "local": local, "error": rem.get("error")}
    remote = rem["remote"]
    newer = version_is_newer(remote, local)
    return {
        "ok": True,
        "local": local,
        "remote": remote,
        "newer": newer,
        "source": rem.get("source"),
        "tag": rem.get("tag"),
        "root": str(ROOT),
        "is_git": (ROOT / ".git").exists(),
    }


def _run_git_pull() -> dict:
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


def _safe_extract_zip(zip_path: Path) -> dict:
    import zipfile
    import shutil
    import tempfile

    preserve_rel = {
        "config/one-mailbox.json",
        "config/multi-accounts.json",
        ".env",
    }
    skip_prefixes = ("data/", "backup-before-")

    with tempfile.TemporaryDirectory(prefix="tk-send-upd-") as td:
        td_path = Path(td)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(td_path)
        kids = [p for p in td_path.iterdir() if p.is_dir()]
        src_root = kids[0] if len(kids) == 1 else td_path
        copied = 0
        for src in src_root.rglob("*"):
            if src.is_dir():
                continue
            rel = src.relative_to(src_root).as_posix()
            if rel.startswith(skip_prefixes) or rel in preserve_rel:
                continue
            if rel.startswith("config/") and rel.endswith(".json") and not rel.endswith(".example.json"):
                if (ROOT / rel).is_file():
                    continue
            dest = ROOT / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            copied += 1
    return {"ok": True, "method": "zip_overlay", "copied": copied, "local": read_local_version()}


def apply_update() -> dict:
    before = read_local_version()
    chk = check_update()
    if not chk.get("ok"):
        return chk
    if not chk.get("newer"):
        return {
            "ok": True,
            "updated": False,
            "reason": "already_latest",
            "local": before,
            "remote": chk.get("remote"),
        }

    if (ROOT / ".git").exists():
        result = _run_git_pull()
        if not result.get("ok"):
            return result
        after = read_local_version()
        return {
            "ok": True,
            "updated": True,
            "method": "git_pull",
            "before": before,
            "after": after,
            "remote": chk.get("remote"),
            "output": result.get("output"),
            "reload_extension": True,
        }

    try:
        import tempfile

        raw = _http_get(ZIP_URL, timeout=60)
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tf:
            tf.write(raw)
            zpath = Path(tf.name)
        try:
            result = _safe_extract_zip(zpath)
        finally:
            try:
                zpath.unlink()
            except Exception:
                pass
        if not result.get("ok"):
            return result
        after = read_local_version()
        return {
            "ok": True,
            "updated": True,
            "method": "zip_overlay",
            "before": before,
            "after": after,
            "remote": chk.get("remote"),
            "copied": result.get("copied"),
            "reload_extension": True,
        }
    except Exception as e:
        log("apply_update zip fail: " + str(e))
        return {
            "ok": False,
            "error": str(e),
            "hint": "git clone the public repo or run Update-SendPlugin.ps1",
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
            elif cmd in ("check_update", "check-update"):
                write_msg(check_update())
            elif cmd in ("apply_update", "update", "pull"):
                write_msg(apply_update())
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
