"""Outbound connection from the owner's PC to the reply-notification relay.

Only the owner PC holds Feishu credentials. Coworker extensions keep pending
events locally until this bridge returns a confirmed Feishu notification.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.request import getproxies
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'vendor'
for wheel in VENDOR.glob('*.whl'):
    if wheel.name.startswith(('websocket_client-', 'filelock-')) and str(wheel) not in sys.path:
        sys.path.insert(0, str(wheel))
CENTER_CONFIG = ROOT / "config" / "reply-relay-center.json"
QUEUE_CONFIG = ROOT / "config" / "one-mailbox.json"


def _log(note: str) -> None:
    path = ROOT / "data" / "logs" / "reply-center.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + note + "\n")


def _post_local(scan: dict, queue_config: dict) -> dict:
    body = json.dumps({"scan": scan}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8767/api/browser-mail/worker/inbox_scan",
        body,
        {"Content-Type": "application/json",
         "Authorization": "Bearer " + queue_config["token"]},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=75) as response:
        return json.load(response)


def process_event(event: dict, queue_config: dict, *, post_local=_post_local) -> tuple[int, dict]:
    if not isinstance(event, dict):
        return 400, {"ok": False, "error": "invalid_event"}
    mailbox = str(event.get("mailbox") or "").strip().lower()
    reply = event.get("reply") or {}
    if not re.fullmatch(r"[^\s@]+@gmail\.com", mailbox):
        return 400, {"ok": False, "error": "invalid_mailbox"}
    table_id = str(event.get("table_id") or "").strip()
    if not table_id:
        table_id = next((str(a.get("feishu_table_id") or "")
            for a in queue_config.get("accounts") or []
            if str(a.get("mailbox") or "").strip().lower() == mailbox), "")
    if not re.fullmatch(r"tbl[A-Za-z0-9]+", table_id):
        return 400, {"ok": False, "error": "table_id_required"}
    if (not isinstance(reply, dict) or
        not re.fullmatch(r"[a-f0-9]{10,40}", str(reply.get("message_id") or "").lower()) or
        not re.fullmatch(r"[^\s@]+@[^\s@]+", str(reply.get("from") or "")) or
        not str(reply.get("at") or "").strip()):
        return 400, {"ok": False, "error": "invalid_reply"}
    try:
        from local_queue import is_historical_reply_notice, parse_gmail_reply_time
    except ImportError:  # pragma: no cover
        from python.local_queue import is_historical_reply_notice, parse_gmail_reply_time  # type: ignore
    parsed_time = parse_gmail_reply_time(str(reply.get("at") or ""))
    if parsed_time and is_historical_reply_notice(queue_config, parsed_time):
        return 200, {"ok": True, "status": "historical_skipped",
                     "gmail_message_id": reply["message_id"]}
    row = {k: reply.get(k) for k in (
        "from", "subject", "snippet", "at", "message_id", "thread_id",
        "thread_url", "body", "body_verified", "suspicious", "spam", "unread",
    ) if k in reply}
    scan = {"mailbox": mailbox, "feishu_table_id": table_id,
            "reason": "remote_reply", "replies_found": True,
            "replies": [row], "inbox_rows": [row], "spam_rows": [], "no_switch": True}
    try:
        result = post_local(scan, queue_config)
    except (TimeoutError, OSError, urllib.error.URLError):
        return 503, {"ok": False, "error": "local_queue_unavailable"}
    except Exception:
        return 503, {"ok": False, "error": "local_queue_failed"}
    if result.get("match_errors"):
        return 503, {"ok": False, "error": "feishu_lookup_failed"}
    if not result.get("replies_found"):
        return 409, {"ok": False, "error": "reply_unmatched"}
    writebacks = result.get("reply_writeback") or []
    if not writebacks or any(w.get("ok") is not True for w in writebacks):
        return 503, {"ok": False, "error": "feishu_writeback_failed"}
    notices = result.get("notify") or []
    good = next((n for n in notices if n.get("ok") and n.get("status") in ("sent", "duplicate")), None)
    if not good:
        return 503, {"ok": False, "error": "group_notice_unconfirmed"}
    return 200, {"ok": True, "status": good["status"],
                 "gmail_message_id": reply["message_id"],
                 "feishu_message_id": good.get("message_id")}


def process_feishu_request(request: dict, queue_config: dict) -> tuple[int, dict]:
    """Execute only the Base calls used by the coworker queue on the owner's PC."""
    try:
        from feishu_candidates import _lark_exe
    except ImportError:  # pragma: no cover
        from python.feishu_candidates import _lark_exe  # type: ignore
    command = request.get("command")
    if command not in ("+record-list", "+record-get", "+record-batch-update"):
        return 400, {"ok": False, "error": "unsupported_command"}
    feishu = queue_config.get("feishu") or {}
    base = str(feishu.get("base_token") or "")
    table = str(request.get("table_id") or "")
    if str(request.get("base_token") or base) != base or not re.fullmatch(r"tbl[A-Za-z0-9]+", table):
        return 403, {"ok": False, "error": "table_not_allowed"}
    exe = _lark_exe()
    if not base or not table or not exe.is_file():
        return 503, {"ok": False, "error": "center_feishu_not_ready"}
    args = [str(exe), "base", command, "--base-token", base, "--table-id", table,
            "--as", "bot", "--profile", str(feishu.get("profile") or "tk-outreach-local")]
    with tempfile.TemporaryDirectory(prefix="tk-team-base-") as temp:
        output = Path(temp) / "output.ndjson"
        if command == "+record-list":
            offset = int(request.get("offset") or 0)
            if offset < 0 or offset > 1000000:
                return 400, {"ok": False, "error": "invalid_offset"}
            args += ["--format", "ndjson", "--limit", "2000", "--offset", str(offset),
                     "--output", str(output), "--overwrite"]
        elif command == "+record-get":
            rid = str(request.get("record_id") or "")
            if not re.fullmatch(r"rec[A-Za-z0-9]+", rid):
                return 400, {"ok": False, "error": "invalid_record_id"}
            args += ["--record-id", rid, "--field-id", "回复时间", "--format", "ndjson",
                     "--output", str(output), "--overwrite"]
        else:
            update = request.get("update") or {}
            records = update.get("update_records") if isinstance(update, dict) else None
            allowed_fields = {"发送状态", "发送时间", "最近发送时间", "邮箱账号", "回复时间"}
            if not isinstance(records, dict) or not 1 <= len(records) <= 100 or any(
                not re.fullmatch(r"rec[A-Za-z0-9]+", str(rid)) or
                not isinstance(fields, dict) or not fields or
                not set(fields).issubset(allowed_fields)
                for rid, fields in records.items()
            ):
                return 400, {"ok": False, "error": "invalid_update"}
            payload = Path(temp) / "update.json"
            payload.write_text(json.dumps(update, ensure_ascii=False), encoding="utf-8")
            args += ["--json", "@" + str(payload)]
        try:
            proc = subprocess.run(args, capture_output=True, timeout=80,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except Exception:
            return 503, {"ok": False, "error": "center_feishu_command_failed"}
        raw = (proc.stdout or proc.stderr or b"").decode("utf-8", errors="replace")
        if proc.returncode != 0:
            return 503, {"ok": False, "error": "center_feishu_rejected", "detail": raw[:300]}
        content = output.read_text(encoding="utf-8") if output.is_file() else ""
        return 200, {"ok": True, "returncode": proc.returncode,
                     "stdout": raw, "output": content}


def main() -> None:
    import websocket
    from filelock import FileLock, Timeout

    lock = FileLock(str(ROOT / "data" / "reply-center-bridge.lock"))
    try:
        lock.acquire(timeout=0)
    except Timeout:
        return

    while True:
        try:
            center = json.loads(CENTER_CONFIG.read_text(encoding="utf-8"))
            if not center.get("relay_url") or not center.get("host_key"):
                raise ValueError("reply relay center is not provisioned")
            url = center["relay_url"].replace("https://", "wss://").rstrip("/") + "/host"
            proxy = getproxies().get("https") or getproxies().get("http")
            proxy_args = {}
            if proxy:
                parsed = urlparse(proxy if "://" in proxy else "http://" + proxy)
                proxy_args = {"http_proxy_host": parsed.hostname,
                              "http_proxy_port": parsed.port, "proxy_type": "http"}
            def on_message(ws, raw):
                item = {}
                try:
                    item = json.loads(raw)
                    if "id" not in item:
                        return
                    event = json.loads(item.get("body") or "{}")
                    queue = json.loads(QUEUE_CONFIG.read_text(encoding="utf-8"))
                    if item.get("kind") == "feishu_cli" and event.get("op") == "feishu_cli":
                        status, result = process_feishu_request(event, queue)
                    else:
                        status, result = process_event(event, queue)
                except Exception:
                    status, result = 503, {"ok": False, "error": "center_processing_failed"}
                if item.get("id"):
                    ws.send(json.dumps({"id": item["id"], "status": status,
                                        "result": result}, ensure_ascii=False))
            app = websocket.WebSocketApp(url,
                header={"Authorization": "Bearer " + center["host_key"]},
                on_message=on_message,
                on_open=lambda _ws: _log("center_connected"),
                on_close=lambda _ws, _code, _reason: _log("center_disconnected"))
            app.run_forever(ping_interval=20, ping_timeout=10, **proxy_args)
        except Exception as exc:
            _log("center_error " + type(exc).__name__)
        time.sleep(5)


if __name__ == "__main__":
    main()
