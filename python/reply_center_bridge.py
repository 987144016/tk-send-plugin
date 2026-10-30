"""Outbound connection from the owner's PC to the reply-notification relay.

Only the owner PC holds Feishu credentials. Coworker extensions keep pending
events locally until this bridge returns a confirmed Feishu notification.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
from urllib.request import getproxies
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
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
    allowed = {str(a.get("mailbox") or "").strip().lower()
               for a in queue_config.get("accounts") or []}
    if mailbox not in allowed:
        return 409, {"ok": False, "error": "mailbox_not_configured"}
    if (not isinstance(reply, dict) or
        not re.fullmatch(r"[a-f0-9]{10,40}", str(reply.get("message_id") or "").lower()) or
        not re.fullmatch(r"[^\s@]+@[^\s@]+", str(reply.get("from") or "")) or
        not str(reply.get("at") or "").strip()):
        return 400, {"ok": False, "error": "invalid_reply"}
    row = {k: reply.get(k) for k in (
        "from", "subject", "snippet", "at", "message_id", "thread_id",
        "thread_url", "body", "body_verified", "suspicious", "spam", "unread",
    ) if k in reply}
    scan = {"mailbox": mailbox, "reason": "remote_reply", "replies_found": True,
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
