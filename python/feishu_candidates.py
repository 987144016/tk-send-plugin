#!/usr/bin/env python3
"""Feishu unsent Gmail candidates for standalone send-plugin.

Schedule/Options path must stay fast: multi-table cache + short CLI timeout +
stale-while-error. Send/enqueue path can force fresh (use_cache=False).

Eligibility (Jim 2026-10-29 / t387u):
- Valid Gmail present (邮箱/公开邮箱 or sole email-like field); creator_name/id optional empty OK
- Never-sent / pending / blank status OR last send time older than 24h
- Cross-BD 24h dedupe: if ANY configured mailbox Feishu table shows that
  email sent within 24h, skip (not only the current table)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

GMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@gmail\.com$", re.I)
UNSENT_STATUS = {"", "pending", "未发送", "待发送", "未发", "ready", "待发"}
BLOCKED_STATUS = {"replied", "bounced", "已回复", "退信"}
# 可疑拦截 is NOT blocked — Jim: can retry later with a new template.
SENT_LIKE = {"sent", "已发送", "已发", "success", "成功", "delivered"}
CROSS_BD_HOURS = 24
SH = ZoneInfo("Asia/Shanghai")
SEND_TIME_KEYS = ("发送时间", "最近发送时间", "首次发送时间")

# Per-table cache so dual-account round-status does not thrash a single key.
_caches: dict[str, dict[str, Any]] = {}
_fetch_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
CACHE_TTL = 60.0          # fresh window
STALE_TTL = 900.0         # serve last good up to 15 min on Feishu fail/timeout
LIST_TIMEOUT_SEC = 10     # never hang Options; was 90s
SOFT_TIMEOUT_SEC = 8


def _scalar(value: Any) -> str:
    if isinstance(value, list):
        parts = []
        for v in value:
            if isinstance(v, dict):
                parts.append(str(v.get("text") or v.get("name") or v.get("id") or ""))
            else:
                parts.append(str(v))
        return ",".join(p for p in parts if p)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or "")
    return str(value or "").strip()


def _lark_exe() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "npm/node_modules/@larksuite/cli/bin/lark-cli.exe"


try:
    from feishu_cli import relay_enabled, run_lark
except ImportError:  # pragma: no cover
    from python.feishu_cli import relay_enabled, run_lark  # type: ignore


def _feishu_cfg(c: dict) -> dict:
    feishu = dict(c.get("feishu") or {})
    if not feishu.get("base_token") or not feishu.get("table_id"):
        root = Path(__file__).resolve().parents[2]  # tiktok-outreach
        cfg_path = root / "config.yaml"
        if cfg_path.is_file():
            try:
                import yaml  # type: ignore
                y = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                ft = y.get("feishu_table") or {}
                feishu.setdefault("base_token", ft.get("base_token"))
                feishu.setdefault("table_id", ft.get("table_id"))
                feishu.setdefault("profile", ft.get("profile") or "tk-outreach-local")
            except Exception:
                pass
    feishu.setdefault("profile", "tk-outreach-local")
    return feishu


def _cache_key(feishu: dict) -> str:
    return f"{(feishu.get('base_token') or '').strip()}|{(feishu.get('table_id') or '').strip()}"


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        lk = _fetch_locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _fetch_locks[key] = lk
        return lk


def _parse_send_dt(raw: Any) -> datetime | None:
    s = _scalar(raw)
    if not s:
        return None
    s = s.strip()
    # Strip trailing zone labels like " (Asia/Shanghai)"
    if " (" in s and s.endswith(")"):
        s = s.split(" (", 1)[0].strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
    ):
        try:
            d = datetime.strptime(s[:19], fmt)
            return d.replace(tzinfo=SH)
        except Exception:
            pass
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=SH)
        return d.astimezone(SH)
    except Exception:
        return None


def _row_last_send(row: dict) -> datetime | None:
    times = []
    for k in SEND_TIME_KEYS:
        d = _parse_send_dt(row.get(k))
        if d is not None:
            times.append(d)
    return max(times) if times else None


def _row_email(row: dict) -> str:
    """Pull sendable Gmail from 邮箱/公开邮箱, common aliases, or a sole email-like field.

    creator_name / 达人ID / status extras are NOT required for eligibility — email alone is enough.
    """
    named_keys = ("邮箱", "公开邮箱", "email", "Email", "Gmail", "gmail", "邮箱地址", "电子邮箱")
    for k in named_keys:
        v = (_scalar(row.get(k)) or "").strip().lower()
        if v and GMAIL_RE.match(v):
            return v
    # Sole email-like scalar among row values (row only has one email field, no name extras).
    hits: list[str] = []
    for k, raw in (row or {}).items():
        if k in ("record_id", "fields", "id"):
            continue
        v = (_scalar(raw) or "").strip().lower()
        if v and "@" in v and GMAIL_RE.match(v):
            hits.append(v)
    uniq = list(dict.fromkeys(hits))
    if len(uniq) == 1:
        return uniq[0]
    return ""


def _configured_tables(c: dict) -> list[dict]:
    """All mailbox Feishu tables from multi-account config (+ top-level feishu)."""
    feishu = _feishu_cfg(c)
    base = (feishu.get("base_token") or "").strip()
    profile = (feishu.get("profile") or "tk-outreach-local").strip()
    seen: set[str] = set()
    out: list[dict] = []
    accounts = list(c.get("accounts") or [])
    if not accounts and isinstance(c.get("mailboxes"), dict):
        for mb, meta in c["mailboxes"].items():
            if isinstance(meta, dict):
                accounts.append({"mailbox": mb, **meta})
    for a in accounts:
        tid = (a.get("feishu_table_id") or "").strip()
        if not tid or tid in seen:
            continue
        seen.add(tid)
        out.append({
            "base_token": (a.get("feishu_base_token") or base).strip() or base,
            "table_id": tid,
            "profile": profile,
            "mailbox": (a.get("mailbox") or "").strip().lower(),
        })
    top_tid = (feishu.get("table_id") or "").strip()
    if top_tid and top_tid not in seen and base:
        seen.add(top_tid)
        out.append({
            "base_token": base,
            "table_id": top_tid,
            "profile": profile,
            "mailbox": (c.get("active_mailbox") or c.get("mailbox") or "").strip().lower(),
        })
    return out


def _list_records(feishu: dict, *, timeout: float = LIST_TIMEOUT_SEC) -> list[dict]:
    exe = _lark_exe()
    if not exe.is_file() and not relay_enabled():
        raise RuntimeError("未找到飞书官方 CLI（lark-cli）")
    base = (feishu.get("base_token") or "").strip()
    table = (feishu.get("table_id") or "").strip()
    profile = (feishu.get("profile") or "tk-outreach-local").strip()
    if not base or not table:
        raise RuntimeError("飞书 base_token / table_id 未配置")
    rows: list[dict] = []
    offset = 0
    with tempfile.TemporaryDirectory(prefix="tk-sp-feishu-") as td:
        while True:
            out = Path(td) / "records.ndjson"
            args = [
                str(exe), "base", "+record-list",
                "--base-token", base, "--table-id", table,
                "--as", "bot", "--profile", profile,
                "--format", "ndjson", "--limit", "2000",
                "--offset", str(offset), "--output", str(out), "--overwrite",
            ]
            proc = run_lark(
                args, capture_output=True, timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            raw = proc.stdout or proc.stderr
            try:
                meta = json.loads(raw)
            except Exception as e:
                raise RuntimeError("飞书记录列表解析失败") from e
            if not out.is_file():
                raise RuntimeError("飞书记录快照未生成: " + str(meta.get("error") or meta)[:200])
            page = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line]
            rows.extend(page)
            if not meta.get("has_more"):
                break
            nxt = meta.get("next_offset", offset + len(page))
            if not page or nxt <= offset:
                break
            offset = nxt
    return rows


def _fetch_table_rows(feishu: dict, *, use_cache: bool, allow_stale: bool, timeout: float | None) -> list[dict]:
    key = _cache_key(feishu)
    now = time.time()
    cached = _caches.get(key) or {}
    age = now - float(cached.get("at") or 0)
    has_rows = isinstance(cached.get("rows"), list)

    if use_cache and has_rows and age < CACHE_TTL:
        return list(cached["rows"])

    to = float(timeout if timeout is not None else (SOFT_TIMEOUT_SEC if allow_stale else LIST_TIMEOUT_SEC))
    lk = _lock_for(key)
    got = lk.acquire(blocking=False)
    if not got:
        if has_rows and allow_stale:
            return list(cached["rows"])
        lk.acquire(blocking=True, timeout=min(3.0, to))
        cached = _caches.get(key) or {}
        if isinstance(cached.get("rows"), list):
            return list(cached["rows"])
        if allow_stale:
            return []
        raise RuntimeError("飞书查询忙，请稍后")

    try:
        cached = _caches.get(key) or {}
        age = time.time() - float(cached.get("at") or 0)
        has_rows = isinstance(cached.get("rows"), list)
        if use_cache and has_rows and age < CACHE_TTL:
            return list(cached["rows"])
        try:
            rows = _list_records(feishu, timeout=to)
            _caches[key] = {"at": time.time(), "rows": rows, "error": None}
            return list(rows)
        except Exception as e:
            if allow_stale and has_rows and age < STALE_TTL:
                _caches[key] = {
                    "at": float(cached.get("at") or 0),
                    "rows": cached["rows"],
                    "error": str(e),
                    "stale": True,
                }
                return list(cached["rows"])
            raise
    finally:
        lk.release()


def find_sent_record(c: dict, mailbox: str, recipient: str) -> dict:
    """Find a sent creator in this mailbox's Feishu table, including older sends.

    The standalone plugin's job files cover only sends made by this package.
    Existing teammates' Feishu rows are the history for earlier senders.
    """
    mb = (mailbox or "").strip().lower()
    rc = (recipient or "").strip().lower()
    if not mb or not rc:
        return {}
    matches = []
    tables = list(_configured_tables(c))
    base_cfg = _feishu_cfg(c)
    known_ids = {t["table_id"] for t in tables}
    for table_id in c.get("reply_history_table_ids") or []:
        if table_id in known_ids:
            continue
        known_ids.add(table_id)
        tables.append({"base_token": base_cfg["base_token"], "table_id": table_id,
                       "profile": base_cfg["profile"], "mailbox": "*", "history": True})
    for table in tables:
        if table["mailbox"] not in (mb, "*"):
            continue
        rows = _fetch_table_rows(table, use_cache=True, allow_stale=False, timeout=20)
        for row in rows:
            if _row_email(row) != rc:
                continue
            owner = _scalar(row.get("邮箱账号")).strip().lower()
            if table.get("history") and owner != mb:
                continue
            if owner and owner != mb:
                continue
            status = _scalar(row.get("发送状态")).strip().lower()
            sent_at = _row_last_send(row)
            if status not in SENT_LIKE and sent_at is None:
                continue
            rid = str(row.get("record_id") or "").strip()
            if rid:
                matches.append((sent_at or datetime.min.replace(tzinfo=SH), {
                    "feishu_record_id": rid,
                    "feishu_table_id": table["table_id"],
                    "recipient": rc,
                    "sender": mb,
                    "creator_name": _scalar(row.get("达人昵称") or row.get("达人名称")),
                    "creator_id": _scalar(row.get("达人ID")),
                }))
    if not matches:
        return {}
    return max(matches, key=lambda item: item[0])[1]


def _cross_recent_send_map(c: dict, *, use_cache: bool, allow_stale: bool, timeout: float | None) -> dict[str, datetime]:
    """email -> latest send datetime across ALL configured mailbox tables."""
    latest: dict[str, datetime] = {}
    for t in _configured_tables(c):
        try:
            rows = _fetch_table_rows(t, use_cache=use_cache, allow_stale=allow_stale, timeout=timeout)
        except Exception:
            # Best-effort: missing peer table should not zero out current table.
            cached = _caches.get(_cache_key(t)) or {}
            rows = list(cached["rows"]) if isinstance(cached.get("rows"), list) else []
        for row in rows:
            email = _row_email(row)
            if not email or not GMAIL_RE.match(email):
                continue
            last = _row_last_send(row)
            if last is None:
                continue
            prev = latest.get(email)
            if prev is None or last > prev:
                latest[email] = last
    return latest


def _row_eligible_local(row: dict, *, cutoff: datetime) -> bool:
    """Eligible on this table alone (before cross-BD skip)."""
    email = _row_email(row)
    if not email or not GMAIL_RE.match(email):
        return False
    status = _scalar(row.get("发送状态")).lower()
    if status in BLOCKED_STATUS:
        return False
    last = _row_last_send(row)
    if last is None:
        # No send time: treat blank/pending/unsent as eligible; also unknown
        # non-sent-like status. Sent-like without time stays ineligible.
        if status in SENT_LIKE:
            return False
        return True
    return last <= cutoff


def _filter_eligible(
    rows: list[dict],
    *,
    limit: int | None = None,
    cross_latest: dict[str, datetime] | None = None,
    now: datetime | None = None,
) -> list[dict]:
    now = now or datetime.now(SH)
    cutoff = now - timedelta(hours=CROSS_BD_HOURS)
    cross_latest = cross_latest or {}
    out: list[dict] = []
    for row in rows:
        if not _row_eligible_local(row, cutoff=cutoff):
            continue
        email = _row_email(row)
        cross_last = cross_latest.get(email)
        if cross_last is not None and cross_last > cutoff:
            # Any mailbox table sent this email within 24h → skip.
            continue
        last = _row_last_send(row)
        out.append({
            "recipient": email,
            "creator_name": _scalar(row.get("显示名")) or _scalar(row.get("达人ID")),
            "creator_id": _scalar(row.get("达人ID")),
            "feishu_record_id": row.get("record_id") or "",
            "send_status": _scalar(row.get("发送状态")) or "pending",
            "last_sent_at": last.isoformat(timespec="seconds") if last else None,
            "eligible_reason": "never_sent" if last is None else f"last_sent_gt_{CROSS_BD_HOURS}h",
        })
        if limit is not None and len(out) >= limit:
            break
    return out


def list_unsent_gmail(
    c: dict,
    *,
    limit: int | None = None,
    use_cache: bool = True,
    allow_stale: bool = True,
    timeout: float | None = None,
) -> list[dict]:
    """Return eligible Gmail creator rows (24h + cross-BD). Prefer cache; never wait 90s for Options."""
    feishu = _feishu_cfg(c)
    rows = _fetch_table_rows(feishu, use_cache=use_cache, allow_stale=allow_stale, timeout=timeout)
    # Peer tables: reuse cache when possible so Options stays fast.
    cross = _cross_recent_send_map(c, use_cache=True, allow_stale=True, timeout=timeout)
    return _filter_eligible(rows, limit=limit, cross_latest=cross)


def count_unsent_gmail(c: dict, *, use_cache: bool = True, allow_stale: bool = True) -> int:
    return len(list_unsent_gmail(c, use_cache=use_cache, allow_stale=allow_stale))


def count_unsent_gmail_soft(c: dict) -> dict:
    """Options/round-status helper: never raises; returns last-good on Feishu timeout.

    Keys: count (int|None), error (str|None), stale (bool), cache_age_sec (float|None)
    """
    feishu = _feishu_cfg(c)
    key = _cache_key(feishu)
    now = time.time()
    cached = _caches.get(key) or {}
    age = now - float(cached.get("at") or 0) if cached.get("at") else None
    try:
        n = count_unsent_gmail(c, use_cache=True, allow_stale=True)
        cached2 = _caches.get(key) or {}
        stale = bool(cached2.get("stale")) or (
            isinstance(age, (int, float)) and age >= CACHE_TTL
        )
        err = cached2.get("error")
        return {
            "count": n,
            "error": err,
            "stale": stale,
            "cache_age_sec": (now - float(cached2.get("at") or now)) if cached2.get("at") else None,
            "eligibility": f"gmail + (未发送 or last_send>{CROSS_BD_HOURS}h) + cross-BD {CROSS_BD_HOURS}h across all mailbox tables",
        }
    except Exception as e:
        if isinstance(cached.get("rows"), list):
            try:
                cross = _cross_recent_send_map(c, use_cache=True, allow_stale=True, timeout=SOFT_TIMEOUT_SEC)
            except Exception:
                cross = {}
            n = len(_filter_eligible(list(cached["rows"]), cross_latest=cross))
            return {"count": n, "error": str(e), "stale": True, "cache_age_sec": age}
        return {"count": None, "error": str(e), "stale": False, "cache_age_sec": age}


def invalidate_cache(table_id: str | None = None) -> None:
    if not table_id:
        for k in list(_caches.keys()):
            _caches[k]["at"] = 0.0
        return
    for k, v in list(_caches.items()):
        if k.endswith("|" + table_id) or ("|" + table_id) in k:
            v["at"] = 0.0
