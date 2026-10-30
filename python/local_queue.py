#!/usr/bin/env python3
"""Standalone Gmail-web send queue (phase 1: one mailbox).

Decoupled from FastMoss / Ziniao / unified BD helper (8765).
Listens on 127.0.0.1:8767. Web-click path only — no Gmail API OAuth.
Does nothing useful while armed=false or template_name empty.
Human-editable params (mailbox / template / Feishu table / pace) live in
config/one-mailbox.json and can be updated via Options → /api/config.
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from account_switch import list_accounts, should_switch, apply_switch, apply_switch_to, next_account, find_account
except ImportError:  # pragma: no cover
    from python.account_switch import list_accounts, should_switch, apply_switch, apply_switch_to, next_account, find_account  # type: ignore

try:
    from feishu_candidates import (
        count_unsent_gmail,
        list_unsent_gmail,
        count_unsent_gmail_soft,
        invalidate_cache as feishu_cache_invalidate,
    )
except ImportError:  # pragma: no cover
    from python.feishu_candidates import (  # type: ignore
        count_unsent_gmail,
        list_unsent_gmail,
        count_unsent_gmail_soft,
        invalidate_cache as feishu_cache_invalidate,
    )

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "one-mailbox.json"
DATA = ROOT / "data"
JOBS = DATA / "jobs"
RECEIPTS = DATA / "receipts"
LOGS = DATA / "logs"
STATE_FILE = DATA / "queue-state.json"
SENT_LOG = DATA / "sent-pace.jsonl"

_lock = threading.RLock()
_scheduler_stop = threading.Event()
_scheduler_thread: threading.Thread | None = None
SCHEDULE_TICK_SECONDS = 20
SCHEDULE_LOG = LOGS / "schedule-enqueue.log"



def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cfg() -> dict:
    return load_json(DEFAULT_CONFIG, {})


def settings_view(c: dict) -> dict:
    """Flat settings + accounts list for Options multi-account UI."""
    mailbox = (c.get("active_mailbox") or c.get("mailbox") or "").strip().lower()
    accounts = list_accounts(c)
    entry = {}
    for a in accounts:
        if (a.get("mailbox") or "").strip().lower() == mailbox:
            entry = a
            break
    if not entry:
        boxes = c.get("mailboxes") or {}
        entry = dict(boxes.get(mailbox) or {})
        entry["mailbox"] = mailbox
    template = (entry.get("template_name") or c.get("template_name") or "").strip()
    feishu = c.get("feishu") or {}
    pace_src = entry.get("pace") if isinstance(entry.get("pace"), dict) else (c.get("pace") or {})
    pace = pace_src or {}
    accounts_out = []
    for a in accounts:
        p = a.get("pace") if isinstance(a.get("pace"), dict) else {}
        accounts_out.append({
            "mailbox": (a.get("mailbox") or "").strip().lower(),
            "template_name": (a.get("template_name") or "").strip(),
            "feishu_table_name": (a.get("feishu_table_name") or "").strip(),
            "feishu_table_id": (a.get("feishu_table_id") or "").strip(),
            "window_minutes": int(p.get("window_minutes") or pace.get("window_minutes") or 30),
            "batch_size": (pace_batch_y(p, pace_batch_y(pace, 15))),
            "pace": {
                "window_minutes": int(p.get("window_minutes") or 30),
                "max_messages_per_window": pace_batch_y(p, 15),
                "daily_limit": int(p.get("daily_limit") or 500),
                "min_gap_seconds": int(p.get("min_gap_seconds") or 90),
            },
            "armed": bool(a.get("armed", True)),
            "bd_key": a.get("bd_key") or "",
            "notes": a.get("notes") or "",
        })
    return {
        "mailbox": mailbox,
        "active_mailbox": mailbox,
        "template_name": template,
        "feishu_table_name": (entry.get("feishu_table_name") or feishu.get("table_name") or "").strip(),
        "feishu_table_id": (entry.get("feishu_table_id") or feishu.get("table_id") or "").strip(),
        "window_minutes": int(pace.get("window_minutes") or 30),
        "batch_size": pace_batch_y(pace, 15),
        "daily_limit": int(pace.get("daily_limit") or 500),
        "min_gap_seconds": int(pace.get("min_gap_seconds") or 90),
        "armed": bool(c.get("armed")),
        "accounts": accounts_out,
        "switch_policy": c.get("switch_policy") or {"mode": "finish_then_next", "keep_test1_on_active": True},
    }


def apply_settings(body: dict) -> dict:
    """Merge Options payload into one-mailbox.json (supports accounts[])."""
    with _lock:
        c = cfg()
        accounts_in = body.get("accounts")
        if isinstance(accounts_in, list) and accounts_in:
            cleaned = []
            seen = set()
            templates = {}
            for raw in accounts_in:
                if not isinstance(raw, dict):
                    continue
                mailbox = (raw.get("mailbox") or "").strip().lower()
                if not mailbox or "@gmail.com" not in mailbox:
                    raise ValueError("each account needs a @gmail.com mailbox")
                if mailbox in seen:
                    raise ValueError(f"duplicate mailbox: {mailbox}")
                seen.add(mailbox)
                template = (raw.get("template_name") or "").strip()
                if not template:
                    raise ValueError(f"template_name required for {mailbox}")
                if template in templates:
                    raise ValueError(f"template '{template}' already used by {templates[template]} — no cross-account reuse")
                templates[template] = mailbox
                pace_in = raw.get("pace") if isinstance(raw.get("pace"), dict) else {}
                wm = int(raw.get("window_minutes") or pace_in.get("window_minutes") or 30)
                if "batch_size" in raw and raw.get("batch_size") is not None and raw.get("batch_size") != "":
                    bs = _int_allow_zero(raw.get("batch_size"), 0)
                elif "max_messages_per_window" in pace_in:
                    bs = _int_allow_zero(pace_in.get("max_messages_per_window"), 15)
                else:
                    bs = 15
                if wm < 1 or wm > 1440:
                    raise ValueError("window_minutes must be 1–1440")
                if bs < 0 or bs > 100:
                    raise ValueError("batch_size (Y people) must be 0–100 (0=temporarily disable account)")
                tname = (raw.get("feishu_table_name") or "").strip()
                tid = (raw.get("feishu_table_id") or "").strip()
                if not tname and not tid:
                    raise ValueError(f"feishu table_name or table_id required for {mailbox}")
                cleaned.append({
                    "mailbox": mailbox,
                    "template_name": template,
                    "template_status": "READY",
                    "feishu_table_name": tname,
                    "feishu_table_id": tid,
                    "bd_key": (raw.get("bd_key") or "").strip(),
                    "pace": {
                        "window_minutes": wm,
                        "max_messages_per_window": bs,
                        "daily_limit": int(pace_in.get("daily_limit") or 500),
                        "min_gap_seconds": int(pace_in.get("min_gap_seconds") or 90),
                    },
                    "armed": bool(raw.get("armed", True)),
                    "notes": raw.get("notes") or "Updated via Options multi-account UI",
                })
            if not cleaned:
                raise ValueError("accounts list empty after validation")
            active = (body.get("active_mailbox") or body.get("mailbox") or cleaned[0]["mailbox"]).strip().lower()
            if not any(a["mailbox"] == active for a in cleaned):
                active = cleaned[0]["mailbox"]
            active_entry = next(a for a in cleaned if a["mailbox"] == active)
            boxes = {}
            for a in cleaned:
                boxes[a["mailbox"]] = {
                    "role": a.get("role") or "multi",
                    "template_name": a["template_name"],
                    "template_status": "READY",
                    "armed": bool(a.get("armed", True)),
                    "feishu_table_name": a["feishu_table_name"],
                    "feishu_table_id": a["feishu_table_id"],
                    "pace": a["pace"],
                    "notes": a.get("notes") or "",
                }
            c["accounts"] = cleaned
            c["mailboxes"] = boxes
            c["active_mailbox"] = active
            c["mailbox"] = active
            c["template_name"] = active_entry["template_name"]
            c["template_status"] = "READY"
            c["feishu"] = {
                **dict(c.get("feishu") or {}),
                "table_name": active_entry["feishu_table_name"],
                "table_id": active_entry["feishu_table_id"],
            }
            c["pace"] = dict(active_entry["pace"])
            # No global forbidden — cross-account enforced in active_mailbox_entry
            c["forbidden_templates"] = []
            sp = body.get("switch_policy") if isinstance(body.get("switch_policy"), dict) else None
            c["switch_policy"] = sp or c.get("switch_policy") or {"mode": "finish_then_next", "keep_test1_on_active": True}
            if c.get("armed") is None:
                c["armed"] = True
            c["schema_version"] = 2
            c["updated_at"] = now_iso()
            c["updated_via"] = "options-ui-multi"
            save_json(DEFAULT_CONFIG, c)
            return settings_view(c)

        # Legacy flat single-account update (still supported)
        mailbox = (body.get("mailbox") or c.get("active_mailbox") or c.get("mailbox") or "").strip().lower()
        if not mailbox or "@gmail.com" not in mailbox:
            raise ValueError("mailbox must be a @gmail.com address")
        template = (body.get("template_name") if "template_name" in body else None)
        if template is None:
            template = (c.get("template_name") or "")
        template = str(template).strip()
        if not template:
            raise ValueError("template_name required")
        # If accounts exist, ensure template not used by another mailbox
        for a in list_accounts(c):
            other = (a.get("mailbox") or "").strip().lower()
            if other and other != mailbox and (a.get("template_name") or "").strip() == template:
                raise ValueError(f"template '{template}' belongs to {other} — no cross-account reuse")
        feishu = dict(c.get("feishu") or {})
        if "feishu_table_name" in body:
            feishu["table_name"] = str(body.get("feishu_table_name") or "").strip()
        if "feishu_table_id" in body:
            feishu["table_id"] = str(body.get("feishu_table_id") or "").strip()
        if not feishu.get("table_name") and not feishu.get("table_id"):
            raise ValueError("feishu table_name or table_id required")
        pace = dict(c.get("pace") or {})
        if "window_minutes" in body:
            wm = int(body.get("window_minutes") or 0)
            if wm < 1 or wm > 1440:
                raise ValueError("window_minutes must be 1–1440")
            pace["window_minutes"] = wm
        if "batch_size" in body:
            bs = _int_allow_zero(body.get("batch_size"), 0)
            if bs < 0 or bs > 100:
                raise ValueError("batch_size (Y people) must be 0–100 (0=temporarily disable account)")
            pace["max_messages_per_window"] = bs
        pace.setdefault("window_minutes", 30)
        pace.setdefault("max_messages_per_window", 15)
        pace.setdefault("daily_limit", 500)
        pace.setdefault("min_gap_seconds", 90)
        boxes = dict(c.get("mailboxes") or {})
        prev = dict(boxes.get(mailbox) or {})
        prev.update({
            "role": prev.get("role") or "multi",
            "template_name": template,
            "template_status": "READY",
            "armed": True if c.get("armed", True) else bool(prev.get("armed")),
            "feishu_table_name": feishu.get("table_name") or prev.get("feishu_table_name") or "",
            "feishu_table_id": feishu.get("table_id") or prev.get("feishu_table_id") or "",
            "pace": pace,
            "notes": prev.get("notes") or "Updated via Options UI",
        })
        if c.get("armed") is None:
            c["armed"] = True
        prev["armed"] = bool(c.get("armed"))
        boxes[mailbox] = prev
        # sync accounts list entry if present
        accounts = list_accounts(c)
        found = False
        for a in accounts:
            if (a.get("mailbox") or "").strip().lower() == mailbox:
                a.update({
                    "template_name": template,
                    "feishu_table_name": prev["feishu_table_name"],
                    "feishu_table_id": prev["feishu_table_id"],
                    "pace": pace,
                    "armed": prev["armed"],
                })
                found = True
                break
        if not found:
            accounts.append({
                "mailbox": mailbox,
                "template_name": template,
                "feishu_table_name": prev["feishu_table_name"],
                "feishu_table_id": prev["feishu_table_id"],
                "pace": pace,
                "armed": prev["armed"],
            })
        c["accounts"] = accounts
        c["active_mailbox"] = mailbox
        c["mailbox"] = mailbox
        c["template_name"] = template
        c["template_status"] = "READY"
        c["feishu"] = feishu
        c["pace"] = pace
        c["mailboxes"] = boxes
        c["updated_at"] = now_iso()
        c["updated_via"] = "options-ui"
        save_json(DEFAULT_CONFIG, c)
        return settings_view(c)



def mailbox_entry(c: dict, target: str = "") -> tuple[str, dict]:
    """按指定邮箱合并账号配置。

    多标签页模式下每个 Gmail 标签页只发自己登录账号的任务，不能再用全局
    active_mailbox 的模板 / 飞书表 / 额度去领别的号的任务。
    """
    mailbox = (target or c.get("active_mailbox") or c.get("mailbox") or "").strip().lower()
    accounts = list_accounts(c)
    entry = {}
    for a in accounts:
        if (a.get("mailbox") or "").strip().lower() == mailbox:
            entry = dict(a)
            break
    if not entry:
        boxes = c.get("mailboxes") or {}
        entry = dict(boxes.get(mailbox) or {})
    template = (entry.get("template_name") or c.get("template_name") or "").strip()
    armed = bool(c.get("armed")) and bool(entry.get("armed", c.get("armed")))
    if not c.get("armed"):
        armed = False
    if not template:
        armed = False
    # Cross-account: forbid every other mailbox's template
    forbidden = list(c.get("forbidden_templates") or [])
    for a in accounts:
        other = (a.get("mailbox") or "").strip().lower()
        ot = (a.get("template_name") or "").strip()
        if other and other != mailbox and ot and ot not in forbidden:
            forbidden.append(ot)
    pace = entry.get("pace") if isinstance(entry.get("pace"), dict) else (c.get("pace") or {})
    feishu = dict(c.get("feishu") or {})
    if entry.get("feishu_table_name"):
        feishu["table_name"] = entry["feishu_table_name"]
    if entry.get("feishu_table_id"):
        feishu["table_id"] = entry["feishu_table_id"]
    merged = {
        **entry,
        "mailbox": mailbox,
        "template_name": template,
        "armed": armed,
        "subject": c.get("subject") or "Tiktok paid collaboration from PoseFree Apparel",
        "forbidden": forbidden,
        "pace": pace or {},
        "token": c.get("token") or "",
        "feishu": feishu,
    }
    return mailbox, merged


def active_mailbox_entry(c: dict) -> tuple[str, dict]:
    return mailbox_entry(c, "")


def assert_template_allowed(template: str, forbidden: list[str]) -> None:
    if not template:
        raise ValueError("template_name empty — wait for this mailbox own Gmail template")
    if template in forbidden:
        raise ValueError(f"forbidden shared/other template: {template}")


def read_pace_events() -> list[dict]:
    if not SENT_LOG.is_file():
        return []
    out = []
    for line in SENT_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _pace_people(event: dict) -> int:
    try:
        n = int(event.get("people") or 1)
    except Exception:
        n = 1
    return max(1, n)



def _int_allow_zero(value, default: int) -> int:
    """int() that keeps explicit 0 (unlike `x or default`)."""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except Exception:
        return default


def pace_batch_y(pace: dict | None, default: int = 15) -> int:
    pace = pace or {}
    if "max_messages_per_window" in pace:
        return _int_allow_zero(pace.get("max_messages_per_window"), default)
    if "batch_size" in pace:
        return _int_allow_zero(pace.get("batch_size"), default)
    return default


def pace_ok(c: dict, mailbox: str) -> tuple[bool, str]:
    pace = c.get("pace") or {}
    window_m = int(pace.get("window_minutes") or 30)
    max_n = pace_batch_y(pace, 15)
    daily = int(pace.get("daily_limit") or 500)
    min_gap = int(pace.get("min_gap_seconds") or 90)
    if max_n <= 0:
        return False, "Y=0 temporarily disabled (Options「一次发多少人」=0)"
    now = time.time()
    events = [e for e in read_pace_events() if e.get("mailbox") == mailbox and e.get("status") == "sent"]
    recent = [e for e in events if now - float(e.get("ts") or 0) <= window_m * 60]
    recent_people = sum(_pace_people(e) for e in recent)
    if recent_people >= max_n:
        return False, f"pace: {recent_people}/{max_n} people in last {window_m}m"
    day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    today = [e for e in events if float(e.get("ts") or 0) >= day_start]
    today_people = sum(_pace_people(e) for e in today)
    if today_people >= daily:
        return False, f"daily limit {daily}"
    if events:
        last = max(float(e.get("ts") or 0) for e in events)
        if now - last < min_gap:
            return False, f"min_gap {min_gap}s"
    return True, "ok"


def append_pace(mailbox: str, job_id: str, status: str, *, people: int = 1) -> None:
    SENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SENT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": time.time(), "at": now_iso(), "mailbox": mailbox,
            "job_id": job_id, "status": status, "people": max(1, int(people or 1)),
        }, ensure_ascii=False) + "\n")


def list_pending() -> list[Path]:
    JOBS.mkdir(parents=True, exist_ok=True)
    paths = list(JOBS.glob("pending-*.json"))

    def _key(path: Path):
        job = load_json(path, {})
        return (str(job.get("created_at") or ""), path.name)

    return sorted(paths, key=_key)


def state() -> dict:
    return load_json(STATE_FILE, {"enabled": False, "current": None, "leases": {}})


def save_state(s: dict) -> None:
    save_json(STATE_FILE, s)


def auth_token(handler: BaseHTTPRequestHandler) -> str:
    h = handler.headers.get("Authorization") or ""
    if h.startswith("Bearer "):
        return h[7:].strip()
    return ""



def next_pace_allowed_ts(c: dict, mailbox: str) -> tuple[float, str]:
    """Earliest unix ts when pace allows another send; reason string."""
    pace = c.get("pace") or {}
    window_m = int(pace.get("window_minutes") or 30)
    max_n = pace_batch_y(pace, 15)
    daily = int(pace.get("daily_limit") or 500)
    min_gap = int(pace.get("min_gap_seconds") or 90)
    now = time.time()
    if max_n <= 0:
        return now + 365 * 24 * 3600, "Y=0 temporarily disabled"
    events = [e for e in read_pace_events() if e.get("mailbox") == mailbox and e.get("status") == "sent"]
    recent = [e for e in events if now - float(e.get("ts") or 0) <= window_m * 60]
    day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    today = [e for e in events if float(e.get("ts") or 0) >= day_start]
    candidates = [now]
    reason = "ok"
    if len(recent) >= max_n:
        oldest = min(float(e.get("ts") or 0) for e in recent)
        candidates.append(oldest + window_m * 60)
        reason = f"pace_window {len(recent)}/{max_n}"
    if len(today) >= daily:
        # next local midnight
        nxt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        from datetime import timedelta
        candidates.append((nxt + timedelta(days=1)).timestamp())
        reason = f"daily_limit {daily}"
    if events:
        last = max(float(e.get("ts") or 0) for e in events)
        if now - last < min_gap:
            candidates.append(last + min_gap)
            if reason == "ok":
                reason = f"min_gap {min_gap}s"
    allowed = max(candidates)
    return allowed, reason


def format_shanghai(ts: float) -> str:
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Shanghai")
        return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M:%S") + " (Asia/Shanghai)"
    except Exception:
        return datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def format_shanghai_iso(ts: float) -> str:
    """飞书表里既有时间格式（2026-10-27T11:55:44+08:00），写入保持一致。"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(ts, ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    except Exception:
        return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def queued_emails() -> set[str]:
    out: set[str] = set()
    JOBS.mkdir(parents=True, exist_ok=True)
    for p in list(JOBS.glob("pending-*.json")) + list(JOBS.glob("active-*.json")):
        job = load_json(p, {})
        r = (job.get("recipient") or "").strip().lower()
        if r:
            out.add(r)
        for item in (job.get("recipients") or []):
            rr = str(item or "").strip().lower()
            if rr:
                out.add(rr)
        for rec in (job.get("records") or []):
            if not isinstance(rec, dict):
                continue
            rr = str(rec.get("recipient") or "").strip().lower()
            if rr:
                out.add(rr)
    return out



def maybe_finish_then_next(*, remaining: int | None = None, sent_in_round: int | None = None) -> str | None:
    """If policy is finish_then_next and current account finished, switch active. Returns new mailbox or None."""
    with _lock:
        c = cfg()
        sp = c.get("switch_policy") or {}
        if (sp.get("mode") or "finish_then_next") != "finish_then_next":
            return None
        accounts = list_accounts(c)
        if len(accounts) < 2:
            return None
        mailbox, entry = active_mailbox_entry(c)
        pace = entry.get("pace") or c.get("pace") or {}
        batch_y = pace_batch_y(pace, 15)
        st = state()
        round_map = dict(st.get("round_sent") or {})
        if sent_in_round is None:
            sent_in_round = int(round_map.get(mailbox) or 0)
        if remaining is None:
            try:
                remaining = count_unsent_gmail(c)
            except Exception:
                remaining = -1  # unknown — only switch on batch full
        if remaining < 0:
            # only batch-based switch when remaining unknown
            if not should_switch(1, int(sent_in_round), batch_y):
                return None
        elif not should_switch(int(remaining), int(sent_in_round), batch_y):
            return None
        nxt = next_account(c)
        if not nxt:
            return None
        new_mb = (nxt.get("mailbox") or "").strip().lower()
        if not new_mb or new_mb == mailbox:
            return None
        c2 = apply_switch(c)
        c2["updated_at"] = now_iso()
        c2["updated_via"] = "finish_then_next"
        # sync top-level feishu/pace/template from apply_switch result
        save_json(DEFAULT_CONFIG, c2)
        round_map[mailbox] = 0
        round_map[new_mb] = int(round_map.get(new_mb) or 0)
        st["round_sent"] = round_map
        st["last_switch"] = {
            "at": now_iso(),
            "from": mailbox,
            "to": new_mb,
            "reason": "finish_then_next",
            "gmail_ui_switch_required": True,
            "note": "JSON active flipped and persisted; stay on new mailbox (no auto-restore). Extension must 影刀-style avatar→account row click in existing tab (no authuser URL, no new window)",
        }
        st["gmail_ui_switch"] = {"required": True, "target": new_mb, "from": mailbox, "at": now_iso()}
        save_state(st)
        try:
            feishu_cache_invalidate()
        except Exception:
            pass
        return new_mb



def cfg_for_account(c: dict, account: dict) -> dict:
    """Shallow cfg copy with feishu/pace/template bound to one account (for per-mailbox remaining)."""
    out = dict(c)
    feishu = dict(c.get("feishu") or {})
    if account.get("feishu_table_name"):
        feishu["table_name"] = account["feishu_table_name"]
    if account.get("feishu_table_id"):
        feishu["table_id"] = account["feishu_table_id"]
    out["feishu"] = feishu
    if isinstance(account.get("pace"), dict):
        pace = dict(c.get("pace") or {})
        pace.update(account["pace"])
        out["pace"] = pace
    if account.get("template_name"):
        out["template_name"] = account["template_name"]
    mb = (account.get("mailbox") or "").strip().lower()
    if mb:
        out["active_mailbox"] = mb
        out["mailbox"] = mb
    return out



def pending_job_breakdown(mailbox: str | None = None) -> dict:
    """Split pending/active into test(立即发送) vs schedule(定时Y)."""
    test_n = 0
    sched_n = 0
    want = (mailbox or "").strip().lower()
    for p in list(JOBS.glob("pending-*.json")) + list(JOBS.glob("active-*.json")):
        j = load_json(p, {})
        sender = (j.get("sender") or "").strip().lower()
        if want and sender != want:
            continue
        reason = (j.get("enqueue_reason") or "").strip().lower()
        if reason in ("test", "test_per_account", "immediate_test"):
            test_n += 1
        else:
            sched_n += 1
    return {"test_pending": test_n, "schedule_pending": sched_n, "pending_jobs": test_n + sched_n}

def pending_jobs_for_mailbox(mailbox: str) -> int:
    mb = (mailbox or "").strip().lower()
    n = 0
    JOBS.mkdir(parents=True, exist_ok=True)
    for p in list(JOBS.glob("pending-*.json")) + list(JOBS.glob("active-*.json")):
        job = load_json(p, {})
        sender = (job.get("sender") or "").strip().lower()
        if sender == mb or (not sender and mb):
            # count jobs explicitly for this sender; ignore empty sender unless single-account
            if sender == mb:
                n += 1
    return n


def account_pace(c: dict, account: dict) -> dict:
    p = account.get("pace") if isinstance(account.get("pace"), dict) else {}
    top = c.get("pace") or {}
    if "max_messages_per_window" in p:
        batch = _int_allow_zero(p.get("max_messages_per_window"), 15)
    else:
        batch = pace_batch_y(top, 15)
    return {
        "window_minutes": int(p.get("window_minutes") or top.get("window_minutes") or 30),
        "max_messages_per_window": batch,
        "min_gap_seconds": int(p.get("min_gap_seconds") or top.get("min_gap_seconds") or 90),
        "daily_limit": int(p.get("daily_limit") or top.get("daily_limit") or 500),
    }


def _armed_eta_fields(*, remaining: int, next_count: int, allowed_ts: float, now: float,
                      pending_n: int, mailbox: str, is_active: bool, feishu_err: str | None,
                      rotate_eta: float | None = None, paused: bool = False,
                      test_pending: int = 0, schedule_pending: int = 0) -> tuple[float | None, str, str]:
    """Return (eta_ts, eta_label, eta_human).

    Never stamp wall-clock "now" as 下一轮. When ready: 「已启动，约 X 分钟后发 Y 人（当前号）」.
    Future pace gate keeps concrete Asia/Shanghai via eta_ts.
    When paused: always 「已暂停」 and no countdown ETA.
    """
    short = (mailbox or "").split("@")[0] or (mailbox or "?")
    if paused:
        return None, "已暂停", "已暂停"
    if feishu_err and remaining <= 0:
        return None, "飞书暂不可用（保留上次剩余）", f"飞书暂不可用：{feishu_err[:80]}"
    if remaining <= 0:
        return None, "本表剩余未发=0（轮到则 finish_then_next 切下一号）", f"本表无剩余（{short}）"
    if not is_active and rotate_eta is not None:
        eta_ts = max(allowed_ts, rotate_eta)
        mins = max(0, int((eta_ts - now + 59) // 60))
        label = "待当前号发完这一批（一次最多 N 人，或该表发空）后轮到本号"
        if allowed_ts > now + 1 and allowed_ts > (rotate_eta or 0):
            label = "切换后还须等本号节奏窗口"
        human = f"约 {mins} 分钟后轮到本号发 {next_count} 人（{short}）"
        return eta_ts, label, human
    if pending_n > 0:
        tp = int(test_pending or 0)
        sp = int(schedule_pending or 0)
        if tp > 0 and sp == 0:
            label = "测发队列待发（立即发送·每号1封密送Y人；≠定时Y）"
            human = f"测发待发 {tp} 封密送（立即发送·每号1封Y人；当前号 {short}）· 定时下一轮仍是 {next_count} 人/窗"
        elif tp > 0:
            label = "测发+定时队列均有待发"
            human = f"测发待发 {tp} + 定时待发 {sp}（当前号 {short}）· 定时Y={next_count}"
        else:
            label = "定时队列待发（按 Y；非立即测发）"
            human = f"定时待发 {pending_n} 封（Y轮；当前号 {short}）"
        return None, label, human
    if allowed_ts > now + 1:
        mins = max(1, int((allowed_ts - now + 59) // 60))
        human = f"定时下一轮：约 {mins} 分钟后发 {next_count} 人（Y；当前号 {short}）"
        return allowed_ts, "节奏窗口未到（定时Y，非测发）", human
    human = f"定时下一轮可入队：{next_count} 人（Y；当前号 {short}）· 立即发送仍只按Y封/号/号"
    return None, "现在可发（定时按Y入队；立即发送=每号按Y封/号）", human


def build_account_schedule_row(c: dict, account: dict, *, is_active: bool, rotate_eta: float | None) -> dict:
    """Per-account next slot. Feishu via soft cache — never block Options on CLI hang."""
    mb = (account.get("mailbox") or "").strip().lower()
    pace = account_pace(c, account)
    batch_y = pace["max_messages_per_window"]
    min_gap = pace["min_gap_seconds"]
    now = time.time()
    soft = count_unsent_gmail_soft(cfg_for_account(c, account))
    feishu_err = soft.get("error")
    remaining = int(soft["count"]) if soft.get("count") is not None else 0
    if soft.get("count") is None and feishu_err:
        remaining = 0
    allowed_ts, pace_reason = next_pace_allowed_ts(cfg_for_account(c, account), mb)
    pending_n = pending_jobs_for_mailbox(mb)
    br = pending_job_breakdown(mb)
    y_disabled = batch_y <= 0
    next_count = 0 if y_disabled else (min(batch_y, remaining) if remaining > 0 else 0)
    paused = (not bool(state().get("enabled"))) or y_disabled
    eta_ts, eta_label, eta_human = _armed_eta_fields(
        remaining=remaining, next_count=next_count, allowed_ts=allowed_ts, now=now,
        pending_n=pending_n, mailbox=mb, is_active=is_active, feishu_err=feishu_err,
        rotate_eta=None if is_active else rotate_eta, paused=paused,
        test_pending=br["test_pending"], schedule_pending=br["schedule_pending"],
    )
    if y_disabled:
        eta_ts = None
        next_count = 0
        eta_label = "暂时禁用（Y=0）"
        eta_human = "暂时禁用本号（一次发多少人=0）"
    if paused and not y_disabled:
        eta_ts = None
        next_count = 0
    return {
        "mailbox": mb,
        "template_name": (account.get("template_name") or "").strip(),
        "is_active": is_active,
        "feishu_table_name": (account.get("feishu_table_name") or "").strip(),
        "feishu_table_id": (account.get("feishu_table_id") or "").strip(),
        "pace": pace,
        "remaining_unsent": remaining if soft.get("count") is not None else None,
        "remaining_stale": bool(soft.get("stale")),
        "paused": paused,
        "y_disabled": y_disabled,
        "next_round_count": next_count,
        "next_round_count_note": (
            "暂时禁用本号（Y=0）" if y_disabled else ("已暂停" if paused else f"这一批最多 {batch_y} 人（剩余未发 {remaining}）")
        ),
        "next_round_eta": None if paused else (format_shanghai(eta_ts) if eta_ts else None),
        "next_round_eta_ts": None if paused else eta_ts,
        "next_round_eta_label": eta_label,
        "next_round_eta_human": eta_human,
        "pending_jobs": pending_n,
        "test_pending": br["test_pending"],
        "schedule_pending": br["schedule_pending"],
        "schedule_next_count": next_count,
        "pace_reason": pace_reason,
        "feishu_error": feishu_err,
        "min_gap_seconds": min_gap,
    }


def build_round_status() -> dict:
    """Fast schedule snapshot for Options. Does not gate the send path.

    Feishu remaining uses soft cache (short timeout + last-good). Send/enqueue
    still uses fresh Feishu when actually queuing mail.
    """
    c = cfg()
    mailbox, entry = active_mailbox_entry(c)
    pace = entry.get("pace") or c.get("pace") or {}
    window_m = int(pace.get("window_minutes") or 30)
    batch_y = pace_batch_y(pace, 15)
    min_gap = int(pace.get("min_gap_seconds") or 90)
    soft = count_unsent_gmail_soft(c)
    feishu_err = soft.get("error")
    remaining = int(soft["count"]) if soft.get("count") is not None else 0
    pending_n = len(list_pending())
    pending_active = pending_jobs_for_mailbox(mailbox)
    br_all = pending_job_breakdown(None)
    br_active = pending_job_breakdown(mailbox)
    allowed_ts, pace_reason = next_pace_allowed_ts(c, mailbox)
    now = time.time()
    paused = not bool(state().get("enabled"))
    next_count = 0 if paused else (min(batch_y, remaining) if remaining > 0 else 0)
    eta_ts, eta_label, eta_human = _armed_eta_fields(
        remaining=remaining, next_count=next_count, allowed_ts=allowed_ts, now=now,
        pending_n=pending_active or pending_n, mailbox=mailbox, is_active=True,
        feishu_err=feishu_err, paused=paused,
        test_pending=br_active.get("test_pending", 0), schedule_pending=br_active.get("schedule_pending", 0),
    )
    if paused:
        eta_ts = None
    switched_to = None
    if soft.get("count") is not None and remaining <= 0 and not soft.get("stale"):
        switched_to = maybe_finish_then_next(
            remaining=remaining,
            sent_in_round=int((state().get("round_sent") or {}).get(mailbox) or 0),
        )
        if switched_to:
            c = cfg()
            mailbox, entry = active_mailbox_entry(c)
            pace = entry.get("pace") or c.get("pace") or {}
            window_m = int(pace.get("window_minutes") or 30)
            batch_y = pace_batch_y(pace, 15)
            min_gap = int(pace.get("min_gap_seconds") or 90)
            soft = count_unsent_gmail_soft(c)
            feishu_err = soft.get("error")
            remaining = int(soft["count"]) if soft.get("count") is not None else 0
            next_count = min(batch_y, remaining) if remaining > 0 else 0
            allowed_ts, pace_reason = next_pace_allowed_ts(c, mailbox)
            pending_n = len(list_pending())
            pending_active = pending_jobs_for_mailbox(mailbox)
            br_all = pending_job_breakdown(None)
            br_active = pending_job_breakdown(mailbox)
            paused = not bool(state().get("enabled"))
            next_count = 0 if paused else next_count
            eta_ts, eta_label, eta_human = _armed_eta_fields(
                remaining=remaining, next_count=next_count, allowed_ts=allowed_ts, now=now,
                pending_n=pending_active or pending_n, mailbox=mailbox, is_active=True,
                feishu_err=feishu_err, paused=paused,
                test_pending=br_active.get("test_pending", 0), schedule_pending=br_active.get("schedule_pending", 0),
            )
            if paused:
                eta_ts = None
            if (not paused) and remaining > 0 and (pending_active or pending_n) == 0 and allowed_ts <= now + 1:
                eta_label = "已切换并停留在新账号；现在可发"
                eta_human = f"已启动，约 0 分钟后发 {next_count} 人（当前号 {mailbox.split('@')[0]}）"
                eta_ts = None
    accounts = list_accounts(c)
    accounts_n = len(accounts)
    st = state()
    round_map = dict(st.get("round_sent") or {})
    sent_in_round = int(round_map.get(mailbox) or 0)
    left_in_round = 0
    if remaining > 0:
        left_in_round = max(0, min(batch_y - sent_in_round, remaining)) if batch_y > sent_in_round else 0
        if left_in_round <= 0 and remaining > 0:
            left_in_round = min(batch_y, remaining)
    rotate_eta = None
    if paused:
        rotate_eta = None
    elif accounts_n >= 2 and remaining > 0:
        start_t = max(now, allowed_ts)
        rotate_eta = start_t + max(0, left_in_round) * max(1, min_gap)
    elif accounts_n >= 2 and remaining <= 0 and soft.get("count") is not None:
        rotate_eta = now
    schedule = []
    total_remaining = 0
    for a in accounts:
        mb = (a.get("mailbox") or "").strip().lower()
        row = build_account_schedule_row(
            c, a,
            is_active=(mb == mailbox),
            rotate_eta=None if mb == mailbox else rotate_eta,
        )
        schedule.append(row)
        if isinstance(row.get("remaining_unsent"), int):
            total_remaining += int(row["remaining_unsent"])
    combined = {
        "accounts": accounts_n,
        "total_remaining_unsent": total_remaining,
        "active_mailbox": mailbox,
        "rotation": "finish_then_next",
        "rotation_note": (
            "一人多号：每个 Gmail 标签页各自登录一个账号，扩展只领本页签登录号的任务，不再点头像切号。"
            "「一次发多少人」= 这一批最多发几个人，发满就停，隔「批次间隔」分钟再发下一批。"
            "多个页签按顺序发送（同一时刻只有 1 封在途）。"
            "本排程面板只读缓存，不阻塞发信。"
        ),
        "next_after_active_eta": None if paused else (format_shanghai(rotate_eta) if rotate_eta and accounts_n >= 2 else None),
        "gmail_ui_switch": st.get("gmail_ui_switch") or {},
        "last_switch": st.get("last_switch") or {},
    }
    return {
        "ok": True,
        "mailbox": mailbox,
        "armed": bool(entry.get("armed")),
        "template_name": entry.get("template_name") or "",
        "feishu": {
            "table_name": (c.get("feishu") or {}).get("table_name") or "",
            "table_id": (c.get("feishu") or {}).get("table_id") or "",
        },
        "pace": {
            "window_minutes": window_m,
            "max_messages_per_window": batch_y,
            "min_gap_seconds": min_gap,
            "daily_limit": int(pace.get("daily_limit") or 500),
        },
        "paused": paused,
        "enabled": (not paused),
        "next_round_eta": None if paused else (format_shanghai(eta_ts) if eta_ts else None),
        "next_round_eta_ts": None if paused else eta_ts,
        "next_round_eta_label": eta_label,
        "next_round_eta_human": eta_human,
        "next_round_count": next_count,
        "next_round_count_note": ("已暂停" if paused else f"下一批 = 最多 {batch_y} 人（剩余未发 {remaining}）"),
        "remaining_unsent": remaining if soft.get("count") is not None else None,
        "remaining_stale": bool(soft.get("stale")),
        "accounts_count": accounts_n,
        "accounts_schedule": schedule,
        "combined": combined,
        "active_ux_note": "「当前 active」= 此刻发件号；finish_then_next 发完切换后会停留在新号（不自动恢复上一号）。立即发送=按Y人密送一封测发（剩余=0跳过）。排程只读、不挡发信。连接并启动会经 Native Messaging 自动拉起本机队列。",
        "switch_policy": c.get("switch_policy") or {"mode": "finish_then_next"},
        "switched_to": switched_to,
        "pending_jobs": pending_n,
        "pending_active": pending_active,
        "test_pending": (br_all.get("test_pending", 0) if isinstance(br_all, dict) else 0),
        "schedule_pending": (br_all.get("schedule_pending", 0) if isinstance(br_all, dict) else 0),
        "schedule_next_count": next_count,
        "test_send_note": "立即发送=按Y人密送一封测发；与定时批次分开显示",
        "pace_reason": pace_reason,
        "feishu_error": feishu_err,
        "writeback": "on_send_success — finish 写回飞书 发送状态/发送时间；inbox_scan 后才切号；回复则群通知(scaffold)",
        "test_send": "立即发送：每个有剩余的账号入队 1 封密送（BCC），收件人=Y=「一次发多少人」（剩余=0跳过）",
        "schedule_note": "round-status 用飞书软缓存；发送路径不依赖本接口",
        "at": now_iso(),
    }


def _forbidden_for_mailbox(c: dict, mailbox: str) -> list[str]:
    """Other accounts' templates are forbidden for this mailbox."""
    forbidden = list(c.get("forbidden_templates") or [])
    mb = (mailbox or "").strip().lower()
    for a in list_accounts(c):
        other = (a.get("mailbox") or "").strip().lower()
        ot = (a.get("template_name") or "").strip()
        if other and other != mb and ot and ot not in forbidden:
            forbidden.append(ot)
    return forbidden


def enqueue_test_per_account() -> dict:
    """立即发送：每个 remaining>0 的账号入队 ONE 封密送（BCC）。

    - remaining_unsent==0 → skip
    - remaining>0 → 入队 1 个 job：recipient_mode=bcc，recipients=前 Y 个邮箱
      （Y = Options「一次发多少人」= pace.max_messages_per_window，且 min(Y, remaining)，上限 100）
    - recipient / feishu_record_id 保留首个地址（日志兼容）；page.js 用 job.recipients 粘贴密送
    - 测发不走 cadence/min_gap；多标签页各领本号任务；不切号等待
    """
    c = cfg()
    accounts = list_accounts(c)
    if not accounts:
        mb = (c.get("active_mailbox") or c.get("mailbox") or "").strip().lower()
        if not mb:
            raise ValueError("未配置账号")
        accounts = [{
            "mailbox": mb,
            "template_name": c.get("template_name"),
            "feishu_table_name": (c.get("feishu") or {}).get("table_name"),
            "feishu_table_id": (c.get("feishu") or {}).get("table_id"),
            "pace": c.get("pace") or {},
            "armed": c.get("armed"),
        }]

    already = queued_emails()
    enqueued: list[dict] = []
    skipped: list[dict] = []
    per_account: list[dict] = []
    JOBS.mkdir(parents=True, exist_ok=True)
    subject = c.get("subject") or "Tiktok paid collaboration from PoseFree Apparel"

    for account in accounts:
        mb = (account.get("mailbox") or "").strip().lower()
        if not mb:
            continue
        template = (account.get("template_name") or "").strip() or (c.get("template_name") or "").strip()
        armed = bool(c.get("armed")) and bool(account.get("armed", c.get("armed"))) and bool(template)
        pace = account.get("pace") if isinstance(account.get("pace"), dict) else (c.get("pace") or {})
        batch_y = pace_batch_y(pace, 15)
        if batch_y > 100:
            batch_y = 100
        if batch_y <= 0:
            skipped.append({
                "mailbox": mb,
                "reason": "y_zero_disabled",
                "remaining_unsent": None,
                "batch_y": 0,
                "note": "一次发多少人=0，暂时禁用本号",
            })
            continue
        if not armed:
            skipped.append({
                "mailbox": mb,
                "reason": "not_armed_or_no_template",
                "remaining_unsent": None,
                "batch_y": batch_y,
            })
            continue
        # Own Options template is always allowed for this mailbox; do not skip on
        # cross-account forbidden list (that bug aborted mid-compose in v0.3.1).
        c_acc = cfg_for_account(c, account)
        try:
            remaining = count_unsent_gmail(c_acc, use_cache=False)
        except Exception as e:
            skipped.append({
                "mailbox": mb,
                "reason": "feishu_error: " + str(e),
                "remaining_unsent": None,
                "batch_y": batch_y,
            })
            continue

        if remaining <= 0:
            skipped.append({
                "mailbox": mb,
                "reason": "no_remaining",
                "remaining_unsent": 0,
                "batch_y": batch_y,
            })
            continue

        try:
            candidates = list_unsent_gmail(c_acc, use_cache=False)
        except Exception as e:
            skipped.append({
                "mailbox": mb,
                "reason": "feishu_list_error: " + str(e),
                "remaining_unsent": remaining,
                "batch_y": batch_y,
            })
            continue

        want = min(batch_y, remaining, 100)
        picked = [x for x in candidates if x["recipient"] not in already][:want]
        if not picked:
            skipped.append({
                "mailbox": mb,
                "reason": "all_queued_or_empty",
                "remaining_unsent": remaining,
                "batch_y": batch_y,
            })
            continue

        emails = [str(x["recipient"]).strip().lower() for x in picked]
        records = [{
            "recipient": str(x["recipient"]).strip().lower(),
            "creator_name": x.get("creator_name") or "",
            "creator_id": x.get("creator_id") or "",
            "feishu_record_id": x.get("feishu_record_id") or "",
        } for x in picked]
        rids = [r["feishu_record_id"] for r in records if r.get("feishu_record_id")]
        job_id = uuid.uuid4().hex
        job = {
            "job_id": job_id,
            "sender": mb,
            "recipient": emails[0],
            "recipients": emails,
            "recipient_mode": "bcc",
            "creator_name": records[0].get("creator_name") or "",
            "creator_id": records[0].get("creator_id") or "",
            "feishu_record_id": records[0].get("feishu_record_id") or "",
            "feishu_record_ids": rids,
            "records": records,
            "subject": subject,
            "gmail_template_name": template,
            "marker": "TK-" + job_id[:12],
            "inline_image_count": 2,
            "enqueue_reason": "test_per_account",
            "people_count": len(emails),
            "created_at": now_iso(),
        }
        path = JOBS / f"pending-{job_id}.json"
        save_json(path, job)
        for em in emails:
            already.add(em)
        row = {
            "job_id": job_id,
            "mailbox": mb,
            "recipient": emails[0],
            "recipients": emails,
            "recipient_mode": "bcc",
            "people_count": len(emails),
            "creator_name": records[0].get("creator_name"),
            "template_name": template,
            "remaining_unsent_before": remaining,
            "path": str(path),
        }
        enqueued.append(row)
        per_account.append({
            "mailbox": mb,
            "batch_y": batch_y,
            "enqueued": 1,
            "people_count": len(emails),
            "remaining_unsent_before": remaining,
            "template_name": template,
        })

    if not enqueued:
        detail_bits = [f"{s['mailbox']}={s['reason']}" for s in skipped] or ["(无账号)"]
        raise ValueError("没有可入队的账号（各表剩余未发均为 0 或不可用）：" + "; ".join(detail_bits))

    first_mb = enqueued[0]["mailbox"]
    last_mb = enqueued[-1]["mailbox"]
    people_total = sum(int(x.get("people_count") or 0) for x in enqueued)
    c_now = cfg()
    cur = (c_now.get("active_mailbox") or c_now.get("mailbox") or "").strip().lower()
    if cur != first_mb:
        c2 = apply_switch_to(c_now, first_mb)
        c2["updated_at"] = now_iso()
        c2["updated_via"] = "enqueue-test-per-account-bcc"
        save_json(DEFAULT_CONFIG, c2)
    with _lock:
        st = state()
        st["gmail_ui_switch"] = {
            "required": False,
            "target": first_mb,
            "from": cur or None,
            "at": now_iso(),
            "reason": "test_per_account",
            "note": "按Y人密送测发：多标签页按 current_mailbox 领取；剩余=0 已跳过；不切号不等节奏",
        }
        st["last_switch"] = {
            "at": now_iso(),
            "from": cur or None,
            "to": first_mb,
            "reason": "test_per_account_start",
            "gmail_ui_switch_required": False,
            "last_enqueued_mailbox": last_mb,
            "note": "Stay on last account after test batch (no auto-restore)",
        }
        save_state(st)

    try:
        feishu_cache_invalidate()
    except Exception:
        pass

    return {
        "ok": True,
        "enqueued": enqueued,
        "skipped": skipped,
        "per_account": per_account,
        "count": len(enqueued),
        "people_count": people_total,
        "accounts_tested": len(per_account),
        "accounts_skipped": len(skipped),
        "reason": "test_per_account",
        "recipient_mode": "bcc",
        "mailbox": first_mb,
        "first_mailbox": first_mb,
        "last_mailbox": last_mb,
        "template_name": enqueued[0].get("template_name") or "",
        "pending_after": len(list_pending()),
        "note": (
            f"已按账号入队测发 {len(enqueued)} 封密送（共 {people_total} 人；每号 1 封 BCC=Options「一次发多少人」Y；跳过 {len(skipped)} 个剩余=0/不可用）。"
            "多标签页各领本号任务立即发送（不切号/不等节奏）。"
        ),
        "at": now_iso(),
    }



def _log_schedule(event: dict) -> None:
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with SCHEDULE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": now_iso(), **event}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def enqueue_schedule_one_account(account: dict, c: dict | None = None) -> dict:
    """定时入队：指定账号 1 封 BCC（recipients=Y 人）。enqueue_reason=schedule，走 pace。

    与「立即发送」同等密送形态；飞书 list_unsent_gmail 已含 24h 跨表去重 / 已回复 / 退信过滤。
    """
    c = c or cfg()
    mb = (account.get("mailbox") or "").strip().lower()
    if not mb:
        raise ValueError("empty mailbox")
    template = (account.get("template_name") or "").strip() or (c.get("template_name") or "").strip()
    armed = bool(c.get("armed")) and bool(account.get("armed", c.get("armed"))) and bool(template)
    pace = account.get("pace") if isinstance(account.get("pace"), dict) else (c.get("pace") or {})
    batch_y = pace_batch_y(pace, 15)
    if batch_y > 100:
        batch_y = 100
    if batch_y <= 0:
        raise ValueError("y_zero_disabled")
    if not armed:
        raise ValueError("not_armed_or_no_template")
    c_acc = cfg_for_account(c, account)
    already = queued_emails()
    try:
        remaining = count_unsent_gmail(c_acc, use_cache=False)
    except Exception as e:
        raise ValueError("feishu_error: " + str(e)) from e
    if remaining <= 0:
        raise ValueError("no_remaining")
    try:
        candidates = list_unsent_gmail(c_acc, use_cache=False)
    except Exception as e:
        raise ValueError("feishu_list_error: " + str(e)) from e
    want = min(batch_y, remaining, 100)
    picked = [x for x in candidates if x["recipient"] not in already][:want]
    if not picked:
        raise ValueError("all_queued_or_empty")
    emails = [str(x["recipient"]).strip().lower() for x in picked]
    records = [{
        "recipient": str(x["recipient"]).strip().lower(),
        "creator_name": x.get("creator_name") or "",
        "creator_id": x.get("creator_id") or "",
        "feishu_record_id": x.get("feishu_record_id") or "",
    } for x in picked]
    rids = [r["feishu_record_id"] for r in records if r.get("feishu_record_id")]
    job_id = uuid.uuid4().hex
    subject = c.get("subject") or "Tiktok paid collaboration from PoseFree Apparel"
    job = {
        "job_id": job_id,
        "sender": mb,
        "recipient": emails[0],
        "recipients": emails,
        "recipient_mode": "bcc",
        "creator_name": records[0].get("creator_name") or "",
        "creator_id": records[0].get("creator_id") or "",
        "feishu_record_id": records[0].get("feishu_record_id") or "",
        "feishu_record_ids": rids,
        "records": records,
        "subject": subject,
        "gmail_template_name": template,
        "marker": "TK-" + job_id[:12],
        "inline_image_count": 2,
        "enqueue_reason": "schedule",
        "people_count": len(emails),
        "created_at": now_iso(),
    }
    JOBS.mkdir(parents=True, exist_ok=True)
    path = JOBS / f"pending-{job_id}.json"
    save_json(path, job)
    try:
        feishu_cache_invalidate()
    except Exception:
        pass
    return {
        "ok": True,
        "job_id": job_id,
        "mailbox": mb,
        "recipients": emails,
        "people_count": len(emails),
        "batch_y": batch_y,
        "remaining_unsent_before": remaining,
        "template_name": template,
        "path": str(path),
        "enqueue_reason": "schedule",
        "at": now_iso(),
    }


def schedule_tick() -> dict:
    """一轮定时入队：各号按自身 X/Y 与 pace，eligible 则入队 1 封 BCC。"""
    c = cfg()
    with _lock:
        st = state()
        enabled = bool(st.get("enabled"))
    if not enabled:
        out = {"ok": True, "action": "skip", "reason": "queue_disabled", "at": now_iso()}
        _log_schedule(out)
        return out
    if not c.get("armed"):
        out = {"ok": True, "action": "skip", "reason": "global_not_armed", "at": now_iso()}
        _log_schedule(out)
        return out

    accounts = list_accounts(c)
    if not accounts:
        mb = (c.get("active_mailbox") or c.get("mailbox") or "").strip().lower()
        if mb:
            accounts = [{
                "mailbox": mb,
                "template_name": c.get("template_name"),
                "feishu_table_name": (c.get("feishu") or {}).get("table_name"),
                "feishu_table_id": (c.get("feishu") or {}).get("table_id"),
                "pace": c.get("pace") or {},
                "armed": c.get("armed"),
            }]

    enqueued: list[dict] = []
    skipped: list[dict] = []
    for account in accounts:
        mb = (account.get("mailbox") or "").strip().lower()
        if not mb:
            continue
        pace = account.get("pace") if isinstance(account.get("pace"), dict) else (c.get("pace") or {})
        batch_y = pace_batch_y(pace, 15)
        if batch_y <= 0:
            skipped.append({"mailbox": mb, "reason": "y_zero_disabled"})
            continue
        template = (account.get("template_name") or "").strip() or (c.get("template_name") or "").strip()
        armed = bool(c.get("armed")) and bool(account.get("armed", c.get("armed"))) and bool(template)
        if not armed:
            skipped.append({"mailbox": mb, "reason": "not_armed_or_no_template"})
            continue
        if pending_jobs_for_mailbox(mb) > 0:
            skipped.append({"mailbox": mb, "reason": "already_pending"})
            continue
        c_acc = cfg_for_account(c, account)
        ok, reason = pace_ok(c_acc, mb)
        if not ok:
            allowed_ts, pace_reason = next_pace_allowed_ts(c_acc, mb)
            skipped.append({
                "mailbox": mb,
                "reason": reason,
                "pace_reason": pace_reason,
                "next_allowed": format_shanghai(allowed_ts),
            })
            continue
        try:
            row = enqueue_schedule_one_account(account, c)
            enqueued.append(row)
            _log_schedule({"action": "enqueued", **row})
        except ValueError as e:
            skipped.append({"mailbox": mb, "reason": str(e)})
            _log_schedule({"action": "skip_account", "mailbox": mb, "reason": str(e)})
        except Exception as e:
            skipped.append({"mailbox": mb, "reason": "error: " + str(e)})
            _log_schedule({"action": "error", "mailbox": mb, "reason": str(e)})

    out = {
        "ok": True,
        "action": "tick",
        "enqueued_count": len(enqueued),
        "skipped_count": len(skipped),
        "enqueued": [{"mailbox": x.get("mailbox"), "job_id": x.get("job_id"), "people_count": x.get("people_count")} for x in enqueued],
        "skipped": skipped,
        "pending_after": len(list_pending()),
        "at": now_iso(),
    }
    if enqueued or any(s.get("reason") not in ("already_pending",) for s in skipped):
        # always log ticks that enqueue; also log non-trivial skips occasionally via always-log for first accounts
        if enqueued:
            pass  # already per-account logged
        _log_schedule({"action": "tick_summary", "enqueued_count": out["enqueued_count"], "skipped_count": out["skipped_count"], "pending_after": out["pending_after"]})
    return out


def _scheduler_loop() -> None:
    # Let HTTP bind first; then tick forever until stop.
    _scheduler_stop.wait(5)
    while not _scheduler_stop.is_set():
        try:
            schedule_tick()
        except Exception as e:
            _log_schedule({"action": "tick_crash", "reason": str(e)})
        _scheduler_stop.wait(SCHEDULE_TICK_SECONDS)


def start_schedule_enqueue_clock() -> None:
    global _scheduler_thread
    if _scheduler_thread is not None and _scheduler_thread.is_alive():
        return
    _scheduler_stop.clear()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop, name="schedule-enqueue", daemon=True
    )
    _scheduler_thread.start()
    _log_schedule({"action": "scheduler_started", "tick_seconds": SCHEDULE_TICK_SECONDS})



def enqueue_from_feishu(count: int = 1, *, reason: str = "test") -> dict:
    """Enqueue helper. reason=test → per-account BCC (see enqueue_test_per_account).

    Non-test (schedule): ONE BCC job on active mailbox with min(count, 15..100) recipients.
    """
    if reason == "test" or reason == "test_per_account":
        return enqueue_test_per_account()
    # schedule / other: prefer dedicated one-account BCC helper (Y from Options)
    if reason in ("schedule", "scheduled", "pace"):
        c = cfg()
        accounts = list_accounts(c)
        mailbox, entry = active_mailbox_entry(c)
        account = find_account(c, mailbox) if mailbox else None
        if not account:
            account = {
                "mailbox": mailbox,
                "template_name": entry.get("template_name"),
                "feishu_table_name": (c.get("feishu") or {}).get("table_name"),
                "feishu_table_id": (c.get("feishu") or {}).get("table_id"),
                "pace": entry.get("pace") if isinstance(entry.get("pace"), dict) else (c.get("pace") or {}),
                "armed": entry.get("armed"),
            }
        row = enqueue_schedule_one_account(account, c)
        return {
            "ok": True,
            "enqueued": [row],
            "count": 1,
            "people_count": row.get("people_count") or 0,
            "reason": "schedule",
            "recipient_mode": "bcc",
            "mailbox": row.get("mailbox"),
            "template_name": row.get("template_name") or "",
            "pending_after": len(list_pending()),
            "note": f"已定时入队 1 封密送（{row.get('people_count')} 人 BCC）。",
            "at": now_iso(),
        }
    count = max(1, min(int(count), 100))
    c = cfg()
    mailbox, entry = active_mailbox_entry(c)
    if not entry.get("armed"):
        raise ValueError("未解锁：armed=false 或 template_name 为空")
    sched_y = pace_batch_y(entry.get("pace") if isinstance(entry.get("pace"), dict) else (c.get("pace") or {}), 15)
    if sched_y <= 0:
        raise ValueError("当前号一次发多少人=0（暂时禁用），跳过入队")
    assert_template_allowed(entry["template_name"], entry["forbidden"])
    already = queued_emails()
    try:
        candidates = list_unsent_gmail(c, use_cache=False)
    except Exception as e:
        raise ValueError("读取飞书未发名单失败：" + str(e)) from e
    picked = [x for x in candidates if x["recipient"] not in already][:count]
    if not picked:
        raise ValueError("没有可入队的未发 Gmail（已发完或均在队列中）")
    JOBS.mkdir(parents=True, exist_ok=True)
    emails = [str(x["recipient"]).strip().lower() for x in picked]
    records = [{
        "recipient": str(x["recipient"]).strip().lower(),
        "creator_name": x.get("creator_name") or "",
        "creator_id": x.get("creator_id") or "",
        "feishu_record_id": x.get("feishu_record_id") or "",
    } for x in picked]
    rids = [r["feishu_record_id"] for r in records if r.get("feishu_record_id")]
    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "sender": mailbox,
        "recipient": emails[0],
        "recipients": emails,
        "recipient_mode": "bcc",
        "creator_name": records[0].get("creator_name") or "",
        "creator_id": records[0].get("creator_id") or "",
        "feishu_record_id": records[0].get("feishu_record_id") or "",
        "feishu_record_ids": rids,
        "records": records,
        "subject": entry.get("subject") or c.get("subject") or "Tiktok paid collaboration from PoseFree Apparel",
        "gmail_template_name": entry["template_name"],
        "marker": "TK-" + job_id[:12],
        "inline_image_count": 2,
        "enqueue_reason": reason,
        "people_count": len(emails),
        "created_at": now_iso(),
    }
    path = JOBS / f"pending-{job_id}.json"
    save_json(path, job)
    enqueued = [{
        "job_id": job_id,
        "recipient": emails[0],
        "recipients": emails,
        "recipient_mode": "bcc",
        "people_count": len(emails),
        "creator_name": records[0].get("creator_name"),
        "path": str(path),
        "mailbox": mailbox,
    }]
    feishu_cache_invalidate()
    return {
        "ok": True,
        "enqueued": enqueued,
        "count": 1,
        "people_count": len(emails),
        "reason": reason,
        "recipient_mode": "bcc",
        "mailbox": mailbox,
        "template_name": entry.get("template_name") or "",
        "pending_after": len(list_pending()),
        "note": f"已入队 1 封密送 pending（{len(emails)} 人 BCC）；扩展会在同标签页拉取并网页点击发送。",
        "at": now_iso(),
    }



def mark_feishu_sent(c: dict, *, record_id: str = "", mailbox: str = "", recipient: str = "",
                      record_ids: list | None = None, recipients: list | None = None) -> dict:
    """Writeback 发送状态/发送时间 on send success via lark-cli (Base OpenAPI v3).

    Must use v3 shape: update_records is {record_id: {field: value}} (NOT v1 array + fields wrapper).
    Accepts one record_id or a list record_ids (BCC batch → write ALL Y rows). Best-effort; never raises.
    """
    ids: list[str] = []
    if record_ids:
        for x in record_ids:
            s = str(x or "").strip()
            if s and s not in ids:
                ids.append(s)
    rid = (record_id or "").strip()
    if rid and rid not in ids:
        ids.insert(0, rid)
    if not ids:
        return {"ok": False, "reason": "missing_record_id"}
    try:
        from feishu_candidates import _feishu_cfg, _lark_exe
    except ImportError:
        from python.feishu_candidates import _feishu_cfg, _lark_exe  # type: ignore
    import subprocess, os, tempfile
    feishu = _feishu_cfg(c)
    exe = _lark_exe()
    if not exe.is_file():
        return {"ok": False, "reason": "no_lark_cli"}
    base = (feishu.get("base_token") or "").strip()
    table = (feishu.get("table_id") or "").strip()
    profile = (feishu.get("profile") or "tk-outreach-local").strip()
    if not base or not table:
        return {"ok": False, "reason": "feishu_not_configured"}
    sent_at = format_shanghai(time.time()).replace(" (Asia/Shanghai)", "")
    mb = (mailbox or "").strip().lower()
    # 飞书“发送状态”是单选：中文选项已补齐（待发送/已发送/已回复/退信/可疑拦截/无邮箱跳过）。
    fields = {
        "发送状态": ["已发送"],
        "发送时间": sent_at,
        "最近发送时间": sent_at,
    }
    if mb:
        fields["邮箱账号"] = mb
    # v3: keyed by record id; field map directly (no per-record "fields" wrapper).
    # BCC: one payload updates ALL Y record ids with the same 发送状态/发送时间.
    payload = {"update_records": {i: dict(fields) for i in ids}}
    recip_log = recipient
    if recipients:
        try:
            recip_log = ",".join(str(x) for x in recipients[:20])
        except Exception:
            recip_log = recipient
    try:
        with tempfile.TemporaryDirectory(prefix="tk-sp-wb-") as td:
            path = Path(td) / "payload.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            args = [
                str(exe), "base", "+record-batch-update",
                "--base-token", base, "--table-id", table,
                "--as", "bot", "--profile", profile,
                "--json", "@" + str(path),
            ]
            proc = subprocess.run(
                args, capture_output=True, timeout=120,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            raw = (proc.stdout or proc.stderr or b"").decode("utf-8", errors="replace")
            try:
                meta = json.loads(raw) if raw.strip() else {}
            except Exception:
                meta = {"raw": raw[:300]}
            err = meta.get("error") if isinstance(meta, dict) else None
            ok = proc.returncode == 0 and not err and (
                meta.get("ok") is True or meta.get("data") is not None or bool(meta)
            )
            LOGS.mkdir(parents=True, exist_ok=True)
            with (LOGS / "feishu-writeback.log").open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "at": now_iso(), "mailbox": mailbox, "recipient": recip_log,
                    "record_id": ids[0], "record_ids": ids, "people_count": len(ids),
                    "table_id": table, "fields": fields, "ok": ok,
                    "returncode": proc.returncode, "meta": meta if isinstance(meta, dict) else str(meta)[:200],
                    "payload_shape": "v3_update_records_object_batch",
                }, ensure_ascii=False) + "\n")
            if not ok:
                return {"ok": False, "reason": "cli_failed", "meta": meta, "sent_at": sent_at,
                        "record_ids": ids, "people_count": len(ids)}
            try:
                feishu_cache_invalidate(table)
            except Exception:
                try:
                    feishu_cache_invalidate()
                except Exception:
                    pass
            return {"ok": True, "record_id": ids[0], "record_ids": ids, "people_count": len(ids),
                    "fields": fields, "sent_at": sent_at, "table_id": table}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


def mark_feishu_replied(c: dict, *, record_id: str, mailbox: str, recipient: str = "") -> dict:
    """把“回复时间”写回飞书（仅当该格为空，绝不覆盖 BD 手填值）。

    只写时间，不动“回复状态”——那是 BD 的人工判断列。
    """
    rid = (record_id or "").strip()
    if not rid:
        return {"ok": False, "reason": "missing_record_id"}
    try:
        from feishu_candidates import _feishu_cfg, _lark_exe
    except ImportError:
        from python.feishu_candidates import _feishu_cfg, _lark_exe  # type: ignore
    import subprocess, os, tempfile
    feishu = _feishu_cfg(c)
    exe = _lark_exe()
    if not exe.is_file():
        return {"ok": False, "reason": "no_lark_cli"}
    base = (feishu.get("base_token") or "").strip()
    table = (feishu.get("table_id") or "").strip()
    profile = (feishu.get("profile") or "tk-outreach-local").strip()
    if not base or not table:
        return {"ok": False, "reason": "feishu_not_configured"}
    replied_at = format_shanghai_iso(time.time())

    def _cli(*args, cwd=None):
        return subprocess.run(
            [str(exe), "base", *args, "--base-token", base, "--table-id", table,
             "--as", "bot", "--profile", profile],
            capture_output=True, timeout=60, cwd=cwd,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    # 先读现有值：已有回复时间（人工填的）不动。record-get 默认写 ndjson 文件，不落 stdout。
    try:
        with tempfile.TemporaryDirectory(prefix="tk-sp-rg-") as td:
            rpath = Path(td) / "rec.ndjson"
            _cli("+record-get", "--record-id", rid, "--field-id", "回复时间",
                 "--format", "ndjson", "--output", "rec.ndjson", "--overwrite", cwd=td)
            if rpath.is_file():
                for line in rpath.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    cur = rec.get("回复时间")
                    if isinstance(cur, list):
                        cur = ",".join(str(x.get("text") if isinstance(x, dict) else x) for x in cur)
                    if str(cur or "").strip():
                        return {"ok": True, "skipped": "reply_time_already_set",
                                "record_id": rid, "reply_time": str(cur)[:40]}
                    break
    except Exception:
        pass  # 读失败就按空处理，宁可补一条时间也不要漏记

    fields = {"回复时间": replied_at}
    payload = {"update_records": {rid: fields}}
    try:
        with tempfile.TemporaryDirectory(prefix="tk-sp-rb-") as td:
            path = Path(td) / "payload.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            proc = _cli("+record-batch-update", "--json", "@" + str(path))
            raw = (proc.stdout or proc.stderr or b"").decode("utf-8", errors="replace")
            try:
                meta = json.loads(raw) if raw.strip() else {}
            except Exception:
                meta = {"raw": raw[:300]}
            ok = proc.returncode == 0 and not (isinstance(meta, dict) and meta.get("error"))
            LOGS.mkdir(parents=True, exist_ok=True)
            with (LOGS / "feishu-reply-writeback.log").open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "at": now_iso(), "mailbox": mailbox, "recipient": recipient,
                    "record_id": rid, "table_id": table, "fields": fields, "ok": ok,
                    "returncode": proc.returncode,
                }, ensure_ascii=False) + "\n")
            return {"ok": ok, "record_id": rid, "reply_time": replied_at, "table_id": table}
    except Exception as e:
        return {"ok": False, "reason": str(e)}



# --- bounce / suspicious-block classifier (v0.3.5) ---
_EMAIL_RE = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_HARD_BOUNCE_RE = re.compile(
    r"undeliverable|mail delivery failed|delivery status notification\s*\(failure\)|"
    r"returned mail|failure notice|delivery failure|user unknown|no such user|"
    r"address not found|recipient address rejected|does not exist|"
    r"550[-\s]?5\.1\.1|5\.1\.1|地址不存在|找不到地址|地址失效|"
    r"mailbox unavailable|invalid recipient|unknown user",
    re.I,
)
_SUSPICIOUS_BLOCK_RE = re.compile(
    r"message blocked|has been blocked|your message to .+ has been blocked|"
    r"blocked\. see technical details|policy.{0,40}block|"
    r"phishing|suspicious message|spam.?like|可能是钓鱼|可能是垃圾|"
    r"邮件被拦截|可疑拦截|内容被拒|被判定为垃圾",
    re.I,
)
_DAEMON_FROM_RE = re.compile(
    r"mailer-daemon|postmaster|mail-daemon|noreply@google\.com|"
    r"mail delivery subsystem",
    re.I,
)


def classify_delivery_notice(*, frm: str = "", subject: str = "", snippet: str = "",
                             at: str = "", bounce_kind: str = "") -> str | None:
    """Return 'hard_bounce' | 'suspicious_block' | None.

    退信 = permanent undeliverable / invalid address.
    可疑拦截 = Gmail Message blocked / phishing-spam style (retry with new template OK).
    """
    kind = (bounce_kind or "").strip().lower()
    if kind in {"hard_bounce", "退信", "bounce", "bounced"}:
        return "hard_bounce"
    if kind in {"suspicious_block", "可疑拦截", "message_blocked", "blocked"}:
        return "suspicious_block"
    blob = " ".join(str(x or "") for x in (frm, subject, snippet, at))
    if _SUSPICIOUS_BLOCK_RE.search(blob):
        return "suspicious_block"
    from_is_daemon = bool(_DAEMON_FROM_RE.search(frm or "")) or bool(
        _DAEMON_FROM_RE.search(blob))
    if from_is_daemon and _HARD_BOUNCE_RE.search(blob):
        return "hard_bounce"
    if from_is_daemon and not _SUSPICIOUS_BLOCK_RE.search(blob):
        # DSN without Message-blocked wording → treat as hard bounce
        if re.search(r"delivery status|undeliver|mailer-daemon|failure", blob, re.I):
            return "hard_bounce"
    return None


def extract_failed_recipient(*texts: str) -> str:
    """Pull original recipient from bounce / Message-blocked notice text."""
    blob = "\n".join(str(t or "") for t in texts)
    patterns = [
        r"Your message to\s+([^\s>]+@[^\s>]+)\s+has been blocked",
        r"message to\s+([^\s>]+@[^\s>]+)\s+has been blocked",
        r"Final-Recipient:\s*rfc822;\s*(\S+)",
        r"Original-Recipient:\s*rfc822;\s*(\S+)",
        r"X-Failed-Recipients:\s*(\S+)",
        r"(?:To|原收件人)[:：]\s*<?([A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})>?",
        r"收件人[:：]\s*<?([A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})>?",
    ]
    for pat in patterns:
        m = re.search(pat, blob, re.I)
        if m:
            em = m.group(1).strip().lower().rstrip(".,;>")
            if "@" in em and not _DAEMON_FROM_RE.search(em):
                return em
    # fallback: first non-daemon email in blob
    for m in _EMAIL_RE.finditer(blob):
        em = m.group(0).lower()
        if not _DAEMON_FROM_RE.search(em):
            return em
    return ""


def mark_feishu_delivery_status(
    c: dict, *, record_id: str, status_label: str, mailbox: str = "",
    recipient: str = "", kind: str = "",
) -> dict:
    """Write 发送状态 to 退信 or 可疑拦截 (v3 batch update)."""
    rid = (record_id or "").strip()
    label = (status_label or "").strip()
    if not rid or label not in {"退信", "可疑拦截"}:
        return {"ok": False, "reason": "bad_args", "record_id": rid, "label": label}
    try:
        from feishu_candidates import _feishu_cfg, _lark_exe
    except ImportError:
        from python.feishu_candidates import _feishu_cfg, _lark_exe  # type: ignore
    import subprocess, os, tempfile
    feishu = _feishu_cfg(c)
    exe = _lark_exe()
    if not exe.is_file():
        return {"ok": False, "reason": "no_lark_cli"}
    base = (feishu.get("base_token") or "").strip()
    table = (feishu.get("table_id") or "").strip()
    profile = (feishu.get("profile") or "tk-outreach-local").strip()
    if not base or not table:
        return {"ok": False, "reason": "feishu_not_configured"}
    fields = {"发送状态": [label]}
    payload = {"update_records": {rid: fields}}
    try:
        with tempfile.TemporaryDirectory(prefix="tk-sp-bounce-") as td:
            path = Path(td) / "payload.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            proc = subprocess.run(
                [str(exe), "base", "+record-batch-update",
                 "--base-token", base, "--table-id", table,
                 "--as", "bot", "--profile", profile,
                 "--json", "@" + str(path)],
                capture_output=True, timeout=120,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            raw = (proc.stdout or proc.stderr or b"").decode("utf-8", errors="replace")
            try:
                meta = json.loads(raw) if raw.strip() else {}
            except Exception:
                meta = {"raw": raw[:300]}
            ok = proc.returncode == 0 and not (isinstance(meta, dict) and meta.get("error"))
            LOGS.mkdir(parents=True, exist_ok=True)
            with (LOGS / "feishu-bounce-writeback.log").open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "at": now_iso(), "mailbox": mailbox, "recipient": recipient,
                    "record_id": rid, "table_id": table, "label": label, "kind": kind,
                    "ok": ok, "returncode": proc.returncode,
                }, ensure_ascii=False) + "\n")
            try:
                feishu_cache_invalidate(table)
            except Exception:
                try:
                    feishu_cache_invalidate()
                except Exception:
                    pass
            return {"ok": ok, "record_id": rid, "label": label, "kind": kind,
                    "recipient": recipient, "table_id": table}
    except Exception as e:
        return {"ok": False, "reason": str(e), "record_id": rid, "label": label}


def apply_bounce_classifications(c: dict, *, mailbox: str, rows: list) -> list:
    """From inbox_scan rows, classify bounce/block notices and write Feishu status."""
    results = []
    seen = set()
    mb = (mailbox or "").strip().lower()
    if not mb:
        return results
    c_rb = c
    acc = find_account(c, mb)
    if acc:
        c_rb = cfg_for_account(c, acc)
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        frm = str(r.get("from") or "").strip().lower()
        subject = str(r.get("subject") or "")
        snippet = str(r.get("snippet") or "")
        at = str(r.get("at") or "")
        kind = classify_delivery_notice(
            frm=frm, subject=subject, snippet=snippet, at=at,
            bounce_kind=str(r.get("bounce_kind") or r.get("kind") or ""),
        )
        if not kind:
            continue
        recipient = str(r.get("failed_recipient") or "").strip().lower()
        if not recipient or "@" not in recipient:
            recipient = extract_failed_recipient(subject, snippet, at, frm)
        if not recipient or "@" not in recipient:
            results.append({"ok": False, "reason": "no_failed_recipient",
                            "kind": kind, "from": frm, "subject": subject[:80]})
            continue
        key = (kind, recipient)
        if key in seen:
            continue
        seen.add(key)
        sent_job = find_sent_job(mb, recipient)
        rid = str(sent_job.get("feishu_record_id") or "").strip()
        if not rid:
            results.append({"ok": False, "reason": "no_sent_job",
                            "kind": kind, "recipient": recipient})
            continue
        label = "退信" if kind == "hard_bounce" else "可疑拦截"
        try:
            wb = mark_feishu_delivery_status(
                c_rb, record_id=rid, status_label=label,
                mailbox=mb, recipient=recipient, kind=kind)
            results.append(wb)
        except Exception as e:
            results.append({"ok": False, "reason": str(e),
                            "kind": kind, "recipient": recipient, "record_id": rid})
    return results



def find_sent_job(mailbox: str, recipient: str) -> dict:
    """按 发件号 + 收件人 找最近一封已发出的任务（拿 feishu_record_id 回写回复时间）。

    BCC 批次：收件人落在 job.recipients / records 内也算命中，并返回该人对应的 record_id。
    """
    mb = (mailbox or "").strip().lower()
    rc = (recipient or "").strip().lower()
    if not mb or not rc:
        return {}
    hits = []
    for pattern in ("done-*.json", "active-*.json", "pending-*.json"):
        for p in JOBS.glob(pattern):
            try:
                job = load_json(p, {}) or {}
            except Exception:
                continue
            if (job.get("sender") or "").strip().lower() != mb:
                continue
            emails = []
            for item in (job.get("recipients") or []):
                em = str(item or "").strip().lower()
                if em:
                    emails.append(em)
            primary = (job.get("recipient") or "").strip().lower()
            if primary and primary not in emails:
                emails.insert(0, primary)
            if rc not in emails:
                continue
            rid = ""
            for rec in (job.get("records") or []):
                if not isinstance(rec, dict):
                    continue
                if str(rec.get("recipient") or "").strip().lower() == rc:
                    rid = str(rec.get("feishu_record_id") or "").strip()
                    break
            if not rid:
                rids = [str(x).strip() for x in (job.get("feishu_record_ids") or []) if str(x).strip()]
                try:
                    idx = emails.index(rc)
                    if idx < len(rids):
                        rid = rids[idx]
                except ValueError:
                    rid = ""
            if not rid:
                rid = str(job.get("feishu_record_id") or "").strip()
            if not rid:
                continue
            hit = dict(job)
            hit["feishu_record_id"] = rid
            hit["recipient"] = rc
            hits.append(hit)
    if not hits:
        return {}
    hits.sort(key=lambda j: str(j.get("created_at") or j.get("requeued_at") or ""), reverse=True)
    return hits[0]


def inbox_scan_blocking(st: dict, mailbox: str) -> bool:
    """待补做收件箱扫描时的 poll 闸门：只挡同一个号，不挡别的号（多账号并行扫描）。"""
    mb = (mailbox or "").strip().lower()
    now = time.time()
    by = st.get("inbox_scan_by_mailbox") or {}
    if isinstance(by, dict):
        for key, item in by.items():
            if not isinstance(item, dict):
                continue
            k = str(key or "").strip().lower()
            if mb and k and k != mb:
                continue
            ts = float(item.get("ts") or 0)
            if ts and now - ts > 600:
                continue  # 超时自动放行，避免一个号卡死整队
            return True
    legacy = st.get("inbox_scan_pending") or {}
    if isinstance(legacy, dict) and legacy:
        lmb = str(legacy.get("mailbox") or "").strip().lower()
        ts = float(legacy.get("ts") or 0)
        if ts and now - ts > 600:
            return False
        if mb and lmb and mb != lmb:
            return False
        return True
    return False


def switch_after_inbox_scan(*, mailbox: str) -> str | None:
    """After inbox scan on `mailbox`, switch for next work. Order: send → scan → switch.

    Prefer next pending job.sender; else finish_then_next when policy says so.
    Stay on switched account (no restore).
    """
    pending = list_pending()
    if pending:
        job = load_json(pending[0], {})
        nxt = (job.get("sender") or "").strip().lower()
        cur = (mailbox or "").strip().lower()
        if nxt and nxt != cur and find_account(cfg(), nxt):
            c2 = apply_switch_to(cfg(), nxt)
            c2["updated_at"] = now_iso()
            c2["updated_via"] = "after_inbox_scan_next_pending"
            save_json(DEFAULT_CONFIG, c2)
            with _lock:
                st = state()
                st["gmail_ui_switch"] = {
                    "required": True,
                    "target": nxt,
                    "from": cur,
                    "at": now_iso(),
                    "reason": "after_inbox_scan_next_pending",
                    "note": "send→inbox_scan→then switch; stay on new mailbox",
                }
                st["last_switch"] = {
                    "at": now_iso(), "from": cur, "to": nxt,
                    "reason": "after_inbox_scan_next_pending",
                    "gmail_ui_switch_required": True,
                }
                st.pop("inbox_scan_pending", None)
                save_state(st)
            return nxt
        with _lock:
            st = state()
            st.pop("inbox_scan_pending", None)
            save_state(st)
        return None
    # No pending: normal finish_then_next (remaining=0 or batch full)
    try:
        rem = count_unsent_gmail(cfg())
    except Exception:
        rem = -1
    st2 = state()
    sent_n = int((st2.get("round_sent") or {}).get(mailbox) or 0)
    switched = maybe_finish_then_next(remaining=rem if rem >= 0 else None, sent_in_round=sent_n)
    with _lock:
        st = state()
        st.pop("inbox_scan_pending", None)
        save_state(st)
    return switched



def ensure_session_token(c: dict | None = None) -> str:
    """Return stable queue session token; persist newly minted ones into config.
    Fixes Options/extension holding a bootstrap token that never got written to
    one-mailbox.json (poll then 401 forever → pending stuck / 立即发送看起来无反应).
    """
    with _lock:
        cfg_now = dict(c or cfg())
        token = (cfg_now.get("token") or "").strip()
        if not token or not token.startswith("gmw_"):
            token = "gmw_" + secrets.token_urlsafe(32)
            cfg_now["token"] = token
            cfg_now["updated_at"] = now_iso()
            cfg_now["updated_via"] = "ensure_session_token"
            save_json(DEFAULT_CONFIG, cfg_now)
        elif cfg_now.get("token") != token:
            cfg_now["token"] = token
            save_json(DEFAULT_CONFIG, cfg_now)
        return token


class Handler(BaseHTTPRequestHandler):
    server_version = "TKSendPluginQueue/0.1"

    def log_message(self, fmt: str, *args) -> None:
        LOGS.mkdir(parents=True, exist_ok=True)
        line = "%s - %s\n" % (now_iso(), fmt % args)
        with (LOGS / "queue.log").open("a", encoding="utf-8") as f:
            f.write(line)

    def _json(self, code: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def _read(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            c = cfg()
            mailbox, entry = active_mailbox_entry(c)
            pace = c.get("pace") or {}
            feishu = c.get("feishu") or {}
            pending_n = len(list_pending())
            self._json(200, {
                "ok": True,
                "phase": 1,
                "armed": bool(entry.get("armed")),
                "mailbox": mailbox,
                "template_name": entry.get("template_name") or "",
                "template_status": entry.get("template_status") or c.get("template_status"),
                "feishu": {
                    "table_name": feishu.get("table_name") or "",
                    "table_id": feishu.get("table_id") or "",
                },
                "pace": {
                    "window_minutes": int(pace.get("window_minutes") or 30),
                    "max_messages_per_window": pace_batch_y(pace, 15),
                    "daily_limit": int(pace.get("daily_limit") or 500),
                    "min_gap_seconds": int(pace.get("min_gap_seconds") or 90),
                },
                "pending": pending_n,
                "accounts_count": len(list_accounts(c)),
                "switch_policy": (c.get("switch_policy") or {}).get("mode") or "finish_then_next",
                "round_status_url": "/api/round-status",
                "at": now_iso(),
            })
            return
        if path == "/api/config":
            c = cfg()
            self._json(200, {"ok": True, "settings": settings_view(c), "at": now_iso()})
            return
        if path == "/api/round-status":
            try:
                self._json(200, build_round_status())
            except Exception as e:
                self._json(500, {"detail": "round-status failed: " + str(e)})
            return
        self._json(404, {"detail": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self._read()
        c = cfg()
        mailbox, entry = active_mailbox_entry(c)


        if path == "/api/session/heal":
            try:
                token = ensure_session_token()
                c = cfg()
                mailbox, entry = active_mailbox_entry(c)
                pending_n = len(list_pending())
                self._json(200, {
                    "ok": True,
                    "token": token,
                    "mailbox": mailbox,
                    "template_name": entry.get("template_name") or "",
                    "armed": bool(entry.get("armed")),
                    "pending": pending_n,
                    "at": now_iso(),
                    "note": "会话已刷新；扩展应用此 token 后即可拉取 pending",
                })
            except Exception as e:
                self._json(500, {"detail": "session heal failed: " + str(e)})
            return

        if path == "/api/schedule-tick":
            try:
                result = schedule_tick()
                self._json(200, result)
            except Exception as e:
                self._json(500, {"detail": "schedule-tick failed: " + str(e)})
            return

        if path == "/api/enqueue-test":
            # Options 「立即发送（按Y人密送一封）」— 1 BCC job per account with remaining>0; skip remaining=0.
            try:
                result = enqueue_test_per_account()
            except ValueError as e:
                self._json(400, {"detail": str(e)})
                return
            except Exception as e:
                self._json(500, {"detail": "enqueue-test failed: " + str(e)})
                return
            self._json(200, result)
            return

        if path == "/api/config":
            try:
                settings = apply_settings(body)
            except ValueError as e:
                self._json(400, {"detail": str(e)})
                return
            except Exception as e:
                self._json(500, {"detail": "config write failed: " + str(e)})
                return
            self._json(200, {
                "ok": True,
                "settings": settings,
                "mailbox": settings.get("mailbox"),
                "path": str(DEFAULT_CONFIG),
                "at": now_iso(),
            })
            return

        if path == "/api/browser-mail/extension-bootstrap":
            # Re-read after possible prior /api/config write in same session.
            # Source of truth = one-mailbox.json active_mailbox (finish_then_next persists here).
            # Do NOT apply body.mailbox — that used to restore the previous account after a switch.
            c = cfg()
            mailbox, entry = active_mailbox_entry(c)
            if not entry.get("armed"):
                self._json(403, {"detail": "未解锁：armed=false 或 template_name 为空（在 Options 填写模板名并保存；禁止群发）"})
                return
            try:
                assert_template_allowed(entry["template_name"], entry["forbidden"])
            except ValueError as e:
                self._json(403, {"detail": str(e)})
                return
            token = ensure_session_token(c)
            # Re-read after possible persist.
            c = cfg()
            mailbox, entry = active_mailbox_entry(c)
            self._json(200, {
                "token": token,
                "account_id": "multi-active",
                "sender": mailbox,
                "template_name": entry["template_name"],
                "feishu": entry.get("feishu") or {},
                "pace": entry.get("pace") or {},
                "session": "ok",
            })
            return

        if path.startswith("/api/browser-mail/worker/"):
            name = path.rsplit("/", 1)[-1]
            token = auth_token(self)
            expected = (entry.get("token") or "").strip() or ensure_session_token(c)
            # Re-bind entry after ensure may have written token.
            c = cfg()
            mailbox, entry = active_mailbox_entry(c)
            expected = (entry.get("token") or "").strip()
            # Multi-tab: bind template/forbidden to the requesting mailbox (job sender
            # or the Gmail tab's current_mailbox), NEVER the global active_mailbox.
            # Otherwise wuxiaoyu's own Options template is treated as "forbidden".
            prefer_mb = (
                str(body.get("sender") or "").strip().lower()
                or str(body.get("current_mailbox") or "").strip().lower()
            )
            if prefer_mb and "@" in prefer_mb:
                mailbox, entry = mailbox_entry(c, prefer_mb)
            if not expected or token != expected:
                self._json(401, {
                    "detail": "独立队列未启动或会话已过期（扩展会尝试自动启动队列；或双击「一点即运行」）",
                    "code": "session_expired",
                    "hint": "Options 点「连接并启动」或「立即发送」会自动 heal 会话",
                })
                return

            if name == "state":
                with _lock:
                    s = state()
                    s["enabled"] = bool(body.get("enabled"))
                    s["updated_at"] = now_iso()
                    save_state(s)
                self._json(200, {"ok": True, "enabled": body.get("enabled")})
                return

            if name == "diag":
                # 扩展每 8 秒最多上报一次内部状态：为什么没领到任务、卡在哪个阶段。
                try:
                    LOGS.mkdir(parents=True, exist_ok=True)
                    with (LOGS / "extension-diag.log").open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"at": now_iso(), **body}, ensure_ascii=False) + "\n")
                except Exception:
                    pass
                self._json(200, {"ok": True, "pending": len(list_pending())})
                return

            if name == "poll":
                # Peek oldest pending; bind to job.sender (1-per-account test).
                # Do NOT overwrite every job onto current active.
                with _lock:
                    s_peek = state()
                    cur = s_peek.get("current") or {}
                    if cur:
                        started = str(cur.get("started") or "")
                        stale = False
                        try:
                            # Asia/Shanghai iso with offset
                            from datetime import datetime
                            ts = datetime.fromisoformat(started)
                            stale = (time.time() - ts.timestamp()) > 210
                        except Exception:
                            stale = True
                        if stale:
                            jid = cur.get("job_id") or ""
                            for ap in list(JOBS.glob(f"active-{jid}.json")):
                                job_keep = load_json(ap, {})
                                job_keep.pop("lease", None)
                                job_keep["requeued_at"] = now_iso()
                                job_keep["requeue_reason"] = "stale_active_lease_reclaim"
                                save_json(JOBS / f"pending-{jid}.json", job_keep)
                                ap.unlink(missing_ok=True)
                            s_peek["current"] = None
                            s_peek["updated_at"] = now_iso()
                            save_state(s_peek)
                        else:
                            self._json(200, {"job": None, "reason": "busy"})
                            return
                    if inbox_scan_blocking(s_peek, str(body.get("current_mailbox") or "").strip().lower()):
                        self._json(200, {"job": None, "reason": "inbox_scan_pending"})
                        return
                    pending = list_pending()
                    if not pending:
                        self._json(200, {"job": None})
                        return
                    # 浏览器当前登录哪个号，就先发那个号的任务：切号是最后手段，
                    # 不能让“队首属于别的号”变成整队停摆的理由。
                    prefer_peek = str(body.get("current_mailbox") or "").strip().lower()
                    job_path = pending[0]
                    if prefer_peek:
                        matched_path = None
                        for path in pending:
                            item = load_json(path, {}) or {}
                            if str(item.get("sender") or "").strip().lower() == prefer_peek:
                                matched_path = path
                                break
                        if not matched_path:
                            # 两个浏览器各登录一个号时，只发本号的任务；
                            # 没有本号任务就空转，绝不跨号切号。
                            self._json(200, {"job": None, "reason": "no_task_for_this_mailbox",
                                             "mailbox": prefer_peek})
                            return
                        job_path = matched_path
                    job_peek = load_json(job_path, {})
                    job_sender = (job_peek.get("sender") or "").strip().lower()
                    # 多标签页模式：请求方报的 current_mailbox 就是它当前登录的号，
                    # 命中本号任务即视为“绑定分配”，不再改全局 active_mailbox、不要求 UI 切号。
                    pinned = bool(prefer_peek) and job_sender == prefer_peek

                c = cfg()
                mailbox, entry = active_mailbox_entry(c)
                if pinned and job_sender:
                    if not find_account(c, job_sender):
                        self._json(200, {"job": None, "reason": f"unknown_job_sender:{job_sender}"})
                        return
                    mailbox, entry = mailbox_entry(c, job_sender)
                elif job_sender and job_sender != mailbox:
                    if not find_account(c, job_sender):
                        self._json(200, {"job": None, "reason": f"unknown_job_sender:{job_sender}"})
                        return
                    c2 = apply_switch_to(c, job_sender)
                    c2["updated_at"] = now_iso()
                    c2["updated_via"] = "poll_job_sender"
                    save_json(DEFAULT_CONFIG, c2)
                    with _lock:
                        st = state()
                        st["gmail_ui_switch"] = {
                            "required": False,
                            "target": job_sender,
                            "from": mailbox,
                            "at": now_iso(),
                            "reason": "poll_job_sender",
                            "note": "Stay on switched mailbox (no restore). Extension 影刀 click.",
                        }
                        save_state(st)
                    c = cfg()
                    mailbox, entry = active_mailbox_entry(c)

                # 配额与总开关都按“任务所属账号”算：两个浏览器各登录一个号时，
                # 不能用 active_mailbox 的额度去管另一个号。
                pace_mailbox = job_sender or mailbox
                pace_account = find_account(c, pace_mailbox) if pace_mailbox else None
                pace_cfg = cfg_for_account(c, pace_account) if pace_account else c
                if not (pace_account or entry or {}).get("armed"):
                    self._json(200, {"job": None, "reason": "not_armed"})
                    return
                # 「立即发送」/测发：不走 cadence/min_gap/window 限额，点了就该立刻 claim。
                job_reason = str((job_peek or {}).get("enqueue_reason") or "").strip().lower()
                is_test_job = job_reason in ("test", "test_per_account", "immediate_test")
                # Y=0 always blocks (including 立即发送 / test), so disabled accounts never claim.
                y_chk = pace_batch_y((pace_cfg or {}).get("pace") or {}, 15)
                if y_chk <= 0:
                    ok, reason = False, "Y=0 temporarily disabled"
                elif is_test_job:
                    ok, reason = True, "test_bypass_pace"
                else:
                    ok, reason = pace_ok(pace_cfg, pace_mailbox)
                if not ok:
                    self._json(200, {"job": None, "reason": reason})
                    return
                with _lock:
                    s = state()
                    if s.get("current"):
                        self._json(200, {"job": None, "reason": "busy"})
                        return
                    prefer = str(body.get("current_mailbox") or "").strip().lower()
                    # Multi-tab: never block claim on gmail_ui_switch; clear orphan/same-mailbox locks.
                    sw0 = dict(s.get("gmail_ui_switch") or {})
                    if sw0.get("required"):
                        tgt = str(sw0.get("target") or "").strip().lower()
                        frm = str(sw0.get("from") or "").strip().lower()
                        if (not tgt) or (frm and tgt == frm) or (prefer and tgt == prefer) or (prefer and not tgt):
                            sw0["required"] = False
                            sw0["cleared_at"] = now_iso()
                            sw0["cleared_via"] = "poll_skip_same_or_pinned"
                            s["gmail_ui_switch"] = sw0
                            save_state(s)
                    if inbox_scan_blocking(s, prefer):
                        self._json(200, {"job": None, "reason": "inbox_scan_pending"})
                        return
                    pending = list_pending()
                    if not pending:
                        self._json(200, {"job": None})
                        return
                    now_ts = time.time()
                    ready = []
                    for path in pending:
                        item = load_json(path, {}) or {}
                        # 历史上的 blocked 标记不再拦任务：程序侧修好后照样要发。
                        if float(item.get("retry_after") or 0) > now_ts:
                            continue
                        ready.append(path)
                    if not ready:
                        self._json(200, {"job": None, "reason": "pending_backoff"})
                        return
                    # 扩展会报告浏览器当前实际登录的账号：有本账号的任务就先发本账号，
                    # 不要为了队首任务去做没必要的切号（切号失败会把整队拖住）。
                    job_path = ready[0]
                    if prefer:
                        for path in ready:
                            item = load_json(path, {}) or {}
                            if str(item.get("sender") or "").strip().lower() == prefer:
                                job_path = path
                                break
                    job = load_json(job_path, {})
                    bound_sender = (job.get("sender") or "").strip().lower() or mailbox
                    if bound_sender != mailbox:
                        # 队首任务属于另一个邮箱：把活动邮箱切过去再领。之前这里直接返回
                        # sender_mismatch_retry，一旦队首 sender 与 cfg.sender 错位，整个
                        # 队列会永久卡住（pending 一直不动的根因之一）。
                        try:
                            c2 = apply_switch_to(c, bound_sender)
                            c2["updated_at"] = now_iso()
                            c2["updated_via"] = "poll_claim_head_sender"
                            save_json(DEFAULT_CONFIG, c2)
                            c = c2
                            mailbox, entry = active_mailbox_entry(c)
                        except Exception as exc:
                            self._json(200, {"job": None, "reason": "switch_to_head_failed: %s" % exc})
                            return
                        if not entry.get("armed"):
                            self._json(200, {"job": None, "reason": "head_sender_not_armed"})
                            return
                        ok2, reason2 = pace_ok(c, mailbox)
                        if not ok2:
                            self._json(200, {"job": None, "reason": reason2})
                            return
                        s["gmail_ui_switch"] = {
                            "required": False, "target": bound_sender, "from": mailbox,
                            "at": now_iso(), "reason": "poll_claim_head_sender",
                            "note": "队首任务属于其他邮箱，先切号再领取",
                        }
                    try:
                        assert_template_allowed(entry["template_name"], entry["forbidden"])
                    except ValueError as e:
                        self._json(200, {"job": None, "reason": str(e)})
                        return
                    job["sender"] = mailbox
                    job["gmail_template_name"] = entry["template_name"]
                    job["subject"] = job.get("subject") or entry["subject"]
                    job["lease"] = secrets.token_hex(8)
                    job["job_id"] = job.get("job_id") or uuid.uuid4().hex
                    if not job.get("marker"):
                        job["marker"] = "TK-" + job["job_id"][:12]
                    job["inline_image_count"] = int(job.get("inline_image_count") or 2)
                    active = JOBS / f"active-{job['job_id']}.json"
                    save_json(active, job)
                    job_path.unlink(missing_ok=True)
                    s["current"] = {"job_id": job["job_id"], "lease": job["lease"], "started": now_iso()}
                    save_state(s)
                self._json(200, {"job": job})
                return

            if name == "progress":
                # Extension may report accountReady / clear gmail_ui_switch after 影刀 switch.
                stage = str(body.get("stage") or "")
                if body.get("account_ready") or body.get("clear_gmail_ui_switch") or ("账号已切换" in stage):
                    with _lock:
                        st = state()
                        sw = dict(st.get("gmail_ui_switch") or {})
                        if sw:
                            sw["required"] = False
                            sw["cleared_at"] = now_iso()
                            sw["cleared_via"] = "worker_progress"
                            st["gmail_ui_switch"] = sw
                            st["updated_at"] = now_iso()
                            save_state(st)
                self._json(200, {"ok": True, "stage": stage})
                return

            if name == "permit":
                # Phase-1: local permit after extension snapshot; still no mass-send unless job queued.
                snap = body.get("prepared_snapshot") or {}
                tname = (snap.get("template_name") or "").strip()
                # Re-bind again from body.sender (multi-tab) so own Options template is never forbidden.
                job_sender = str(body.get("sender") or "").strip().lower()
                if job_sender and "@" in job_sender:
                    mailbox, entry = mailbox_entry(c, job_sender)
                own = (entry.get("template_name") or "").strip()
                # Options-configured template for THIS mailbox is ALWAYS allowed.
                # Cross-account forbidden list must NOT abort mid-compose.
                if tname and own and tname != own:
                    self._json(403, {"detail": f"snapshot template mismatch: {tname} != {own}"})
                    return
                self._json(200, {"ok": True, "permit": True, "mailbox": mailbox, "template": own or tname})
                return

            if name == "finish":
                # Order (Jim): send success → Feishu writeback → inbox scan (same mailbox) → THEN switch.
                job_snapshot = {}
                with _lock:
                    s = state()
                    receipt = body.get("receipt") or {}
                    job_id = body.get("job_id") or ""
                    status_early = (receipt.get("status") or "").strip().lower()
                    active_now = list(JOBS.glob(f"active-{job_id}.json")) if job_id else []
                    if (job_id and not active_now and status_early in ("paused", "uncertain")
                            and list(JOBS.glob(f"pending-{job_id}.json"))):
                        # 同一任务被扩展反复上报：已经重新排队，就不再重复写回执、
                        # 不再覆盖切号目标，否则 target 会和 job.sender 永久错位。
                        self._json(200, {"ok": True, "duplicate": True, "need_inbox_scan": False,
                                         "note": "该任务已重新排队，忽略重复上报"})
                        return
                    if job_id and not active_now and not list(JOBS.glob(f"pending-{job_id}.json")):
                        # 任务已结束（done 或从未存在）：重复上报同样忽略，避免刷回执。
                        self._json(200, {"ok": True, "duplicate": True, "need_inbox_scan": False,
                                         "note": "该任务已结束，忽略重复上报"})
                        return
                    for pattern in (f"active-{job_id}.json", f"pending-{job_id}.json"):
                        found = list(JOBS.glob(pattern))
                        if found:
                            loaded = load_json(found[0], {})
                            job_snapshot = loaded if isinstance(loaded, dict) else {}
                            break
                    receipt_sig = "%s|%s|%s" % (job_id, status_early, str(receipt.get("error") or "")[:120])
                    last_receipt = s.get("last_receipt") or {}
                    if (last_receipt.get("sig") == receipt_sig
                            and time.time() - float(last_receipt.get("ts") or 0) < 60):
                        # 扩展未清 current 时会每 2.5 秒重报同一回执；这里只认一次。
                        self._json(200, {"ok": True, "duplicate": True, "need_inbox_scan": False,
                                         "note": "重复回执已忽略"})
                        return
                    s["last_receipt"] = {"sig": receipt_sig, "ts": time.time()}
                    RECEIPTS.mkdir(parents=True, exist_ok=True)
                    save_json(RECEIPTS / f"{job_id or 'unknown'}-{int(time.time())}.json", {
                        "at": now_iso(),
                        "job_id": job_id,
                        "receipt": receipt,
                        "mailbox": mailbox,
                        "feishu_record_id": job_snapshot.get("feishu_record_id") or "",
                        "recipient": job_snapshot.get("recipient") or "",
                    })
                    if receipt.get("status") == "sent":
                        # 记账按“这封实际是谁发的”：多标签页时全局 active_mailbox 可能是另一个号。
                        # BCC：一封密送算 people=Y 人，填满本窗「一次发多少人」。
                        pace_mb = (str(job_snapshot.get("sender") or "") or "").strip().lower() or mailbox
                        people_n = int(job_snapshot.get("people_count") or 0)
                        if people_n < 1:
                            people_n = len(job_snapshot.get("recipients") or []) or 1
                        append_pace(pace_mb, job_id, "sent", people=people_n)
                        round_map = dict(s.get("round_sent") or {})
                        round_map[pace_mb] = int(round_map.get(pace_mb) or 0) + people_n
                        s["round_sent"] = round_map
                    status = (receipt.get("status") or "").strip().lower()
                    err = str(receipt.get("error") or "")
                    enqueue_reason = str(job_snapshot.get("enqueue_reason") or "")
                    is_test = enqueue_reason in ("test_per_account", "enqueue-test", "test")
                    mid_compose = bool(re.search(
                        r"forbidden template|snapshot template mismatch|unexpected_existing_draft|prepared_draft_missing|compose_changed|头像|switch|账号|account|超时|timeout|waiting Gmail|影刀",
                        err,
                        re.I,
                    ))
                    # Test/立即发送: never abandon mid-compose — always requeue paused/uncertain
                    # (except hard Gmail send-limit which stays done/paused upstream).
                    hard_limit = bool(re.search(r"gmail_limit|reached.*limit.*sending|已达到发送.*上限", err, re.I))
                    requeue = (
                        status in ("paused", "uncertain")
                        and not hard_limit
                        and (is_test or mid_compose)
                    )
                    for p in list(JOBS.glob(f"active-{job_id}.json")):
                        if requeue:
                            # Keep job for retry; do not mark done so claim can continue after switch.
                            job_keep = load_json(p, {}) or job_snapshot
                            job_keep.pop("lease", None)
                            job_keep["requeued_at"] = now_iso()
                            job_keep["requeue_reason"] = err[:240]
                            retries = int(job_keep.get("retry_count") or 0) + 1
                            job_keep["retry_count"] = retries
                            # 退避只是让这个任务先排队等一会儿，绝不改成 blocked：
                            # 切号/选择器失败是程序侧问题，任务本身仍然要发出去。
                            job_keep["retry_after"] = time.time() + min(60 * retries, 300)
                            job_keep.pop("status", None)
                            job_keep.pop("blocked_reason", None)
                            save_json(JOBS / f"pending-{job_id}.json", job_keep)
                            p.unlink(missing_ok=True)
                        else:
                            p.rename(JOBS / f"done-{job_id}.json")
                    cur = s.get("current") or {}
                    if cur.get("job_id") == job_id:
                        s["current"] = None
                    if requeue:
                        # Keep gmail_ui_switch required so extension retries 影刀 switch.
                        sw = dict(s.get("gmail_ui_switch") or {})
                        # Multi-tab: do not re-arm avatar switch gate after pause.
                        sw["required"] = False
                        sender = str(job_snapshot.get("sender") or "").strip().lower()
                        if sender:
                            sw["target"] = sender
                        sw["at"] = now_iso()
                        sw["reason"] = "requeue_no_switch_gate"
                        s["gmail_ui_switch"] = sw
                    save_state(s)

                writeback = None
                need_inbox_scan = False
                if receipt.get("status") == "sent":
                    # Bind Feishu table to the mailbox that just sent (not a premature next account).
                    c_wb = cfg()
                    sent_mb = (job_snapshot.get("sender") or mailbox or "").strip().lower()
                    acc = find_account(c_wb, sent_mb) if sent_mb else None
                    if acc:
                        c_wb = cfg_for_account(c_wb, acc)
                    wb_ids = []
                    for rec in (job_snapshot.get("records") or []):
                        if isinstance(rec, dict):
                            rid0 = str(rec.get("feishu_record_id") or "").strip()
                            if rid0 and rid0 not in wb_ids:
                                wb_ids.append(rid0)
                    for rid0 in (job_snapshot.get("feishu_record_ids") or []):
                        s0 = str(rid0 or "").strip()
                        if s0 and s0 not in wb_ids:
                            wb_ids.append(s0)
                    one = str(job_snapshot.get("feishu_record_id") or "").strip()
                    if one and one not in wb_ids:
                        wb_ids.append(one)
                    wb_recips = [str(x).strip().lower() for x in (job_snapshot.get("recipients") or []) if str(x).strip()]
                    if not wb_recips and job_snapshot.get("recipient"):
                        wb_recips = [str(job_snapshot.get("recipient")).strip().lower()]
                    writeback = mark_feishu_sent(
                        c_wb,
                        record_id=wb_ids[0] if wb_ids else "",
                        record_ids=wb_ids,
                        mailbox=sent_mb or mailbox,
                        recipient=str(job_snapshot.get("recipient") or ""),
                        recipients=wb_recips,
                    )
                    scan_mb = (sent_mb or mailbox or "").strip().lower()
                    with _lock:
                        st = state()
                        scan_item = {
                            "at": now_iso(),
                            "ts": time.time(),
                            "mailbox": scan_mb,
                            "job_id": job_id,
                            "recipient": job_snapshot.get("recipient") or "",
                            "recipients": wb_recips,
                            "feishu_record_id": job_snapshot.get("feishu_record_id") or "",
                            "feishu_record_ids": wb_ids,
                            "marker": job_snapshot.get("marker") or "",
                            "writeback": writeback,
                        }
                        # 每个号各存一份：A 号待补扫时不能把 B 号一起挡住。
                        by = dict(st.get("inbox_scan_by_mailbox") or {})
                        by[scan_mb] = scan_item
                        st["inbox_scan_by_mailbox"] = by
                        st["inbox_scan_pending"] = scan_item
                        save_state(st)
                    need_inbox_scan = True

                # Do NOT switch here — extension must scan inbox on this mailbox first.
                self._json(200, {
                    "ok": True,
                    "switched_to": None,
                    "need_inbox_scan": need_inbox_scan,
                    "writeback": writeback,
                    "inbox_scan": {
                        "mailbox": (job_snapshot.get("sender") or mailbox or ""),
                        "recipient": job_snapshot.get("recipient") or "",
                        "marker": job_snapshot.get("marker") or "",
                    } if need_inbox_scan else None,
                    "note": "send→writeback→inbox_scan→then switch; stay after switch",
                })
                return

            if name == "inbox_scan":
                # Extension posts DOM inbox-scan result for the mailbox that just sent.
                scan = body.get("scan") or body
                replies = scan.get("replies") or []
                scanned_mb = (scan.get("mailbox") or mailbox or "").strip().lower()
                replies_found = bool(scan.get("replies_found")) or bool(replies)
                rows_seen = scan.get("inbox_rows") or []
                if rows_seen:
                    # 收件箱首屏所有非本人邮件先落盘，便于按邮箱匹配“谁回了/谁退信”。
                    LOGS.mkdir(parents=True, exist_ok=True)
                    with (LOGS / "inbox-rows.jsonl").open("a", encoding="utf-8") as f:
                        f.write(json.dumps({
                            "at": now_iso(), "mailbox": scanned_mb,
                            "job_id": scan.get("job_id") or "",
                            "row_count": len(rows_seen), "rows": rows_seen[:80],
                        }, ensure_ascii=False) + "\n")
                notify = None
                if replies_found:
                    # Scaffold: Feishu group notify only when reply found (wire chat later).
                    notify = {
                        "queued": True,
                        "channel": "feishu_group",
                        "status": "scaffold",
                        "mailbox": scanned_mb,
                        "replies": replies[:20],
                        "note": "Reply found — Feishu group notify placeholder (not sent yet)",
                    }
                    LOGS.mkdir(parents=True, exist_ok=True)
                    with (LOGS / "reply-notify-scaffold.log").open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"at": now_iso(), **notify}, ensure_ascii=False) + "\n")

                # 回写飞书“回复时间”：只认“这个号确实给它发过信”的来信。
                # 退信/可疑拦截：由 classify_delivery_notice 单独写发送状态（不写回复时间）。
                reply_writebacks = []
                bounce_writebacks = []
                if replies_found and scanned_mb:
                    c_rb = cfg()
                    acc_rb = find_account(c_rb, scanned_mb)
                    if acc_rb:
                        c_rb = cfg_for_account(c_rb, acc_rb)
                    seen_from = set()
                    for r in replies[:20]:
                        frm = str(r.get("from") or "").strip().lower()
                        if not frm or "@" not in frm or frm in seen_from:
                            continue
                        sent_job = find_sent_job(scanned_mb, frm)
                        rid = str(sent_job.get("feishu_record_id") or "")
                        if not rid:
                            continue
                        seen_from.add(frm)
                        # 可疑/垃圾邮件里的回信同样是回复：照常写回复时间，只做标记。
                        flagged = {"suspicious": bool(r.get("suspicious")), "spam": bool(r.get("spam"))}
                        try:
                            wb = mark_feishu_replied(
                                c_rb, record_id=rid, mailbox=scanned_mb, recipient=frm)
                            reply_writebacks.append({**wb, **flagged})
                        except Exception as e:
                            reply_writebacks.append({"ok": False, "reason": str(e),
                                                     "recipient": frm, **flagged})
                    if reply_writebacks:
                        LOGS.mkdir(parents=True, exist_ok=True)
                        with (LOGS / "reply-writeback.log").open("a", encoding="utf-8") as f:
                            f.write(json.dumps({
                                "at": now_iso(), "mailbox": scanned_mb,
                                "spam_rows": len(scan.get("spam_rows") or []),
                                "results": reply_writebacks,
                            }, ensure_ascii=False) + "\n")

                # 退信 / 可疑拦截：从 inbox_rows + replies + spam_rows 分类写回发送状态。
                bounce_rows = []
                for src in (rows_seen, replies, scan.get("spam_rows") or []):
                    for item in (src or []):
                        if isinstance(item, dict):
                            bounce_rows.append(item)
                if scanned_mb and bounce_rows:
                    c_b = cfg()
                    bounce_writebacks = apply_bounce_classifications(
                        c_b, mailbox=scanned_mb, rows=bounce_rows)
                    if bounce_writebacks:
                        LOGS.mkdir(parents=True, exist_ok=True)
                        with (LOGS / "bounce-classify.log").open("a", encoding="utf-8") as f:
                            f.write(json.dumps({
                                "at": now_iso(), "mailbox": scanned_mb,
                                "results": bounce_writebacks,
                            }, ensure_ascii=False) + "\n")

                # 清掉本号的“待补扫”标记（别的号的标记保留）。
                scan_key = (scanned_mb or mailbox or "").strip().lower()
                with _lock:
                    st = state()
                    by_scan = dict(st.get("inbox_scan_by_mailbox") or {})
                    by_scan.pop(scan_key, None)
                    if by_scan:
                        st["inbox_scan_by_mailbox"] = by_scan
                    else:
                        st.pop("inbox_scan_by_mailbox", None)
                    legacy_scan = st.get("inbox_scan_pending") or {}
                    if str(legacy_scan.get("mailbox") or "").strip().lower() == scan_key:
                        st.pop("inbox_scan_pending", None)
                    save_state(st)

                # NOW switch (after inbox scan on this mailbox). 多标签页模式传 no_switch。
                switched = None
                if not scan.get("no_switch"):
                    switched = switch_after_inbox_scan(mailbox=scanned_mb or mailbox)
                self._json(200, {
                    "ok": True,
                    "replies_found": replies_found,
                    "replies": replies[:20],
                    "notify": notify,
                    "reply_writeback": reply_writebacks,
                    "bounce_writeback": bounce_writebacks,
                    "spam_rows": len(scan.get("spam_rows") or []),
                    "switched_to": switched,
                    "note": "inbox_scan done; 回复时间空值回写; 退信/可疑拦截已分类写发送状态; no_switch 时不切号",
                })
                return

            self._json(404, {"detail": f"unknown worker op: {name}"})
            return

        self._json(404, {"detail": "not found"})


def main() -> None:
    global DEFAULT_CONFIG
    parser = argparse.ArgumentParser(description="TK standalone Gmail-web send queue (phase 1)")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args()
    DEFAULT_CONFIG = Path(args.config)
    c = cfg()
    host = ((c.get("listen") or {}).get("host")) or "127.0.0.1"
    port = int(((c.get("listen") or {}).get("port")) or 8767)
    mailbox, entry = active_mailbox_entry(c)
    feishu = c.get("feishu") or {}
    pace = c.get("pace") or {}
    DATA.mkdir(parents=True, exist_ok=True)
    JOBS.mkdir(parents=True, exist_ok=True)
    print(f"[send-plugin] queue http://{host}:{port}")
    print(f"[send-plugin] active_mailbox={mailbox} armed={entry.get('armed')} template={entry.get('template_name')!r}")
    print(f"[send-plugin] feishu table={feishu.get('table_name')!r} id={feishu.get('table_id')!r}")
    print(f"[send-plugin] pace every {pace.get('window_minutes')}min / {pace.get('max_messages_per_window')} people (editable in Options)")
    print(f"[send-plugin] pending jobs={len(list_pending())} — schedule clock auto-enqueues BCC by pace X/Y when enabled")
    start_schedule_enqueue_clock()
    print(f"[send-plugin] schedule-enqueue clock started (tick={SCHEDULE_TICK_SECONDS}s)")
    httpd = ThreadingHTTPServer((host, port), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _scheduler_stop.set()
        print("\n[send-plugin] stopped")


if __name__ == "__main__":
    main()
