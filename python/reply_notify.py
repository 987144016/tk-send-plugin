"""Send one Feishu group notice per verified Gmail reply message.

Each installation owns its CLI profile and group target. No credentials are
shipped in this package. A durable claim prevents a timeout from resending a
message whose Feishu acceptance is unknown.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
from datetime import datetime, timezone

LEDGER = Path(__file__).resolve().parents[1] / "data" / "reply-notifications"
_lock = threading.Lock()


def _save(path: Path, value: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _send_lark(settings: dict, key: str, body: str) -> dict:
    exe = Path(os.environ.get("APPDATA", "")) / "npm/node_modules/@larksuite/cli/bin/lark-cli.exe"
    if not exe.is_file():
        raise FileNotFoundError("lark_cli_missing")
    proc = subprocess.run([
        str(exe), "im", "+messages-send", "--as", "bot",
        "--profile", settings["profile"], "--chat-id", settings["chat_id"],
        "--text", "-", "--idempotency-key", key, "--json",
    ], input=body.encode("utf-8"), capture_output=True, timeout=45,
       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    raw = (proc.stdout or proc.stderr or b"").decode("utf-8", errors="replace")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("lark_result_unreadable") from exc
    if proc.returncode != 0 or result.get("ok") is not True:
        raise RuntimeError("lark_rejected")
    data = result.get("data") or {}
    if not data.get("message_id") or data.get("chat_id") != settings["chat_id"]:
        raise RuntimeError("lark_receipt_unverified")
    return result


def notify_reply(config: dict, mailbox: str, reply: dict, contact: dict, *,
                 ledger: Path = LEDGER, transport=None) -> dict:
    settings = config.get("reply_notify") or {}
    if settings.get("enabled") is not True:
        return {"status": "disabled", "ok": False}
    chat_id = str(settings.get("chat_id") or "").strip()
    profile = str(settings.get("profile") or "").strip()
    message_id = str(reply.get("message_id") or "").strip().lower()
    if not re.fullmatch(r"oc_[A-Za-z0-9]+", chat_id) or not profile:
        return {"status": "failed", "ok": False, "error": "group_not_configured"}
    if not re.fullmatch(r"[a-f0-9]{10,40}", message_id):
        return {"status": "failed", "ok": False, "error": "gmail_message_id_missing"}
    identity = "|".join((chat_id, mailbox.lower(), message_id))
    key = "send-plugin-reply-" + hashlib.sha256(identity.encode()).hexdigest()[:32]
    ledger.mkdir(parents=True, exist_ok=True)
    path = ledger / (key + ".json")
    full_body = str(reply.get("body") or "").strip() if reply.get("body_verified") else ""
    snippet = " ".join(str(reply.get("snippet") or "").split())[:500]
    body_line = ("回信原文：\n" + full_body) if full_body else (
        "收件箱预览（非全文，请打开原邮件核对）：" + snippet if snippet else
        "回信正文暂未取得，请打开原邮件核对。")
    creator = str(contact.get("creator_name") or contact.get("creator_id") or reply.get("from") or "").strip()
    body = "\n".join(filter(None, [
        "📩 达人回信",
        "达人：" + creator,
        "达人邮箱：" + str(reply.get("from") or ""),
        "收件邮箱：" + mailbox,
        "回信时间：" + str(reply.get("at") or "待核对"),
        body_line,
        "打开原邮件：" + str(reply.get("thread_url") or "") if reply.get("thread_url") else "",
    ]))
    with _lock:
        if path.exists():
            previous = json.loads(path.read_text(encoding="utf-8"))
            return {"status": "duplicate" if previous.get("status") == "sent" else previous.get("status", "uncertain"),
                    "ok": previous.get("status") == "sent", "notification_id": key,
                    "message_id": previous.get("feishu_message_id"),
                    "sent_at": previous.get("sent_at")}
        if not full_body:
            return {"status": "body_pending", "ok": False}
        claim = {"status": "sending", "notification_id": key,
                 "gmail_message_id": message_id, "mailbox": mailbox,
                 "creator_email": reply.get("from"), "chat_id": chat_id,
                 "at": datetime.now(timezone.utc).isoformat()}
        _save(path, claim)
    try:
        receipt = (transport or _send_lark)(settings, key, body)
        data = receipt.get("data") or {}
        if receipt.get("ok") is not True or data.get("chat_id") != chat_id or not data.get("message_id"):
            raise RuntimeError("lark_receipt_unverified")
        claim.update(status="sent", feishu_message_id=data["message_id"],
                     sent_at=datetime.now(timezone.utc).isoformat())
    except FileNotFoundError:
        claim.update(status="failed", error="lark_cli_missing")
    except subprocess.TimeoutExpired:
        claim.update(status="uncertain", error="lark_timeout")
    except Exception as exc:
        claim.update(status="uncertain", error=type(exc).__name__)
    with _lock:
        _save(path, claim)
    return {"status": claim["status"], "ok": claim["status"] == "sent",
            "notification_id": key, "message_id": claim.get("feishu_message_id"),
            "sent_at": claim.get("sent_at"), "error": claim.get("error")}
