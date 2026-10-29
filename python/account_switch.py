# -*- coding: utf-8 -*-
"""Multi-account switch helpers (Phase B scaffold).

finish_then_next: when active account finishes its Y batch for the round
or Feishu remaining unsent for that table is 0, move active to the next
account in config accounts list (wrap-around).

Wired: Options multi-account UI + local_queue maybe_finish_then_next (finish + round-status).
Do not mass-send from this module.

Test send (立即发送): 1 per configured account with remaining>0; skip remaining=0
(no Gmail switch to empty tables). Stay on last switched account (no restore).
"""
from __future__ import annotations
from typing import Any


def list_accounts(cfg: dict) -> list[dict]:
    accounts = cfg.get("accounts")
    if isinstance(accounts, list) and accounts:
        return [a for a in accounts if isinstance(a, dict) and a.get("mailbox")]
    # fallback: legacy mailboxes map
    boxes = cfg.get("mailboxes") or {}
    out = []
    for mailbox, entry in boxes.items():
        if str(mailbox).startswith("_"):
            continue
        e = dict(entry or {})
        e["mailbox"] = mailbox
        out.append(e)
    return out


def active_index(cfg: dict) -> int:
    accounts = list_accounts(cfg)
    active = (cfg.get("active_mailbox") or cfg.get("mailbox") or "").strip().lower()
    for i, a in enumerate(accounts):
        if (a.get("mailbox") or "").strip().lower() == active:
            return i
    return 0 if accounts else -1


def should_switch(remaining_unsent: int, sent_in_round: int, batch_size: int) -> bool:
    """True when current account finished its round batch or has nothing left.

    batch_size<=0 (Y=0 temporarily disabled) always rotates away so a disabled
    active mailbox does not block finish_then_next.
    """
    if batch_size is not None and int(batch_size) <= 0:
        return True
    if remaining_unsent <= 0:
        return True
    if batch_size > 0 and sent_in_round >= batch_size:
        return True
    return False


def _account_batch_y(account: dict) -> int:
    """Read Y=pace.max_messages_per_window; keep explicit 0."""
    pace = account.get("pace") if isinstance(account.get("pace"), dict) else {}
    if "max_messages_per_window" in pace and pace.get("max_messages_per_window") is not None and pace.get("max_messages_per_window") != "":
        try:
            return int(pace.get("max_messages_per_window"))
        except Exception:
            return 15
    if "batch_size" in pace and pace.get("batch_size") is not None and pace.get("batch_size") != "":
        try:
            return int(pace.get("batch_size"))
        except Exception:
            return 15
    if account.get("batch_size") is not None and account.get("batch_size") != "":
        try:
            return int(account.get("batch_size"))
        except Exception:
            return 15
    return 15


def next_account(cfg: dict) -> dict | None:
    """Next mailbox for finish_then_next; skip Y=0 (temporarily disabled) accounts."""
    accounts = list_accounts(cfg)
    if not accounts:
        return None
    if len(accounts) < 2:
        return accounts[0]
    i = active_index(cfg)
    n = len(accounts)
    for step in range(1, n + 1):
        cand = accounts[(i + step) % n]
        if _account_batch_y(cand) > 0:
            return cand
    return None  # all accounts Y=0


def find_account(cfg: dict, mailbox: str) -> dict | None:
    wanted = (mailbox or "").strip().lower()
    if not wanted:
        return None
    for a in list_accounts(cfg):
        if (a.get("mailbox") or "").strip().lower() == wanted:
            return a
    return None


def _bind_account(cfg: dict, account: dict) -> dict:
    """Shallow-updated cfg with active_mailbox / feishu / template / pace from account."""
    out = dict(cfg)
    mailbox = (account.get("mailbox") or "").strip().lower()
    out["active_mailbox"] = mailbox
    out["mailbox"] = mailbox
    if account.get("template_name"):
        out["template_name"] = account["template_name"]
    feishu = dict(out.get("feishu") or {})
    if account.get("feishu_table_name"):
        feishu["table_name"] = account["feishu_table_name"]
    if account.get("feishu_table_id"):
        feishu["table_id"] = account["feishu_table_id"]
    out["feishu"] = feishu
    if isinstance(account.get("pace"), dict):
        pace = dict(out.get("pace") or {})
        pace.update(account["pace"])
        out["pace"] = pace
    return out


def apply_switch(cfg: dict) -> dict:
    """Return a shallow-updated cfg with active_mailbox moved to next account.
    Caller persists. Does not arm or enqueue.

    IMPORTANT: this only flips JSON active account + Feishu/template for that
    mailbox. The Chrome extension MUST then simulate a human Gmail switch in the
    existing tab (avatar → switch account / same-tab authuser). Never open a new
    Chrome window; never reuse another mailbox's template.
    """
    nxt = next_account(cfg)
    if not nxt:
        return cfg
    return _bind_account(cfg, nxt)


def apply_switch_to(cfg: dict, mailbox: str) -> dict:
    """Flip active to a specific mailbox (for test-per-account / poll job.sender).
    Caller persists. No-op if mailbox unknown. Stay on target — no restore.
    """
    account = find_account(cfg, mailbox)
    if not account:
        return cfg
    return _bind_account(cfg, account)
