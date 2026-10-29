# -*- coding: utf-8 -*-
"""Patch local_queue.py for 0.3.1 immediate-send fix."""
from pathlib import Path
import re

p = Path("python/local_queue.py")
text = p.read_text(encoding="utf-8")
orig = text
changes = []

# 1) Fix prefer NameError: define prefer before inbox_scan_blocking in second lock
old1 = '''                with _lock:
                    s = state()
                    if s.get("current"):
                        self._json(200, {"job": None, "reason": "busy"})
                        return
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
                        # 历史上的 blocked 标记不再挡任务：程序侧修好后照样要发。
                        if float(item.get("retry_after") or 0) > now_ts:
                            continue
                        ready.append(path)
                    if not ready:
                        self._json(200, {"job": None, "reason": "pending_backoff"})
                        return
                    # 扩展会报告浏览器当前实际登录的账号：有本账号的任务就先发本账号，
                    # 不要为了队首任务去做没必要的切号（切号失败会把整队拖住）。
                    prefer = str(body.get("current_mailbox") or "").strip().lower()
                    job_path = ready[0]'''

# The Chinese comments may have encoding differences - use a more flexible approach
# Find the unique English-free anchors

marker_busy = 'self._json(200, {"job": None, "reason": "busy"})\n                        return\n                    if inbox_scan_blocking(s, prefer):'
if marker_busy not in text:
    raise SystemExit("marker_busy not found")

# Replace the prefer use-before-define: insert prefer= before the inbox_scan_blocking call
# and remove the later duplicate prefer = assignment (keep one)

text2 = text.replace(
    'self._json(200, {"job": None, "reason": "busy"})\n                        return\n                    if inbox_scan_blocking(s, prefer):',
    'self._json(200, {"job": None, "reason": "busy"})\n                        return\n'
    '                    prefer = str(body.get("current_mailbox") or "").strip().lower()\n'
    '                    # Multi-tab: never block claim on gmail_ui_switch; clear orphan/same-mailbox locks.\n'
    '                    sw0 = dict(s.get("gmail_ui_switch") or {})\n'
    '                    if sw0.get("required"):\n'
    '                        tgt = str(sw0.get("target") or "").strip().lower()\n'
    '                        frm = str(sw0.get("from") or "").strip().lower()\n'
    '                        if (not tgt) or (frm and tgt == frm) or (prefer and tgt == prefer) or (prefer and not tgt):\n'
    '                            sw0["required"] = False\n'
    '                            sw0["cleared_at"] = now_iso()\n'
    '                            sw0["cleared_via"] = "poll_skip_same_or_pinned"\n'
    '                            s["gmail_ui_switch"] = sw0\n'
    '                            save_state(s)\n'
    '                    if inbox_scan_blocking(s, prefer):',
    1,
)
if text2 == text:
    raise SystemExit("prefer fix did not apply")
text = text2
changes.append("fix prefer NameError + auto-clear same-mailbox gmail_ui_switch on poll")

# Remove duplicate prefer = later in same block (keep first)
# After our insert there will be two prefer = lines. Remove the second occurrence near job_path = ready[0]
dup_pat = re.compile(
    r'(ready\.append\(path\)\n'
    r'                    if not ready:\n'
    r'                        self\._json\(200, \{"job": None, "reason": "pending_backoff"\}\)\n'
    r'                        return\n'
    r'                    #[^\n]*\n'
    r'                    #[^\n]*\n'
    r'                    prefer = str\(body\.get\("current_mailbox"\) or ""\)\.strip\(\)\.lower\(\)\n'
    r'                    job_path = ready\[0\])',
    re.M,
)
# Simpler: just replace the second "prefer = str(body.get..." before job_path = ready[0]
idx_first = text.find('prefer = str(body.get("current_mailbox") or "").strip().lower()')
idx_second = text.find('prefer = str(body.get("current_mailbox") or "").strip().lower()', idx_first + 1)
if idx_second < 0:
    raise SystemExit("second prefer assignment not found")
# Find context around second to make sure it's the right one
snippet = text[idx_second-80:idx_second+120]
print("SECOND PREFER CONTEXT:\n", snippet)
text = text[:idx_second] + text[idx_second:].replace(
    'prefer = str(body.get("current_mailbox") or "").strip().lower()\n                    job_path = ready[0]',
    'job_path = ready[0]',
    1,
)
changes.append("remove duplicate prefer assignment")

# 2) Bypass pace for test / immediate jobs when claiming matched pending
# Insert before: ok, reason = pace_ok(pace_cfg, pace_mailbox)
old_pace = '''                ok, reason = pace_ok(pace_cfg, pace_mailbox)
                if not ok:
                    self._json(200, {"job": None, "reason": reason})
                    return'''
if old_pace not in text:
    # try finding just the lines
    if 'ok, reason = pace_ok(pace_cfg, pace_mailbox)' not in text:
        raise SystemExit("pace_ok call not found")
    print("WARN: exact pace block mismatch, using line replace")

new_pace = '''                # 「立即发送」/测发：不走 cadence/min_gap/window 限额，点了就该立刻 claim。
                job_reason = str((job_peek or {}).get("enqueue_reason") or "").strip().lower()
                is_test_job = job_reason in ("test", "test_per_account", "immediate_test")
                if is_test_job:
                    ok, reason = True, "test_bypass_pace"
                else:
                    ok, reason = pace_ok(pace_cfg, pace_mailbox)
                if not ok:
                    self._json(200, {"job": None, "reason": reason})
                    return'''
if old_pace in text:
    text = text.replace(old_pace, new_pace, 1)
    changes.append("bypass pace for test_per_account jobs")
else:
    raise SystemExit("pace block exact match failed")

# 3) When pinned (tab mailbox == job.sender), never set gmail_ui_switch
# The elif job_sender != mailbox block sets switch — keep for legacy but skip when pinned already handled.
# Also change enqueue_test to NOT set required=True

# Find enqueue gmail_ui_switch block
enq_marker = 'st["gmail_ui_switch"] = {\n            "required": True,\n            "target": first_mb,'
if enq_marker not in text:
    # encoding may differ on Chinese note - try partial
    idx = text.find('"reason": "test_per_account"')
    print("test_per_account reason idx", idx)
    # Find nearby gmail_ui_switch assignment in enqueue_test_per_account
    idx = text.find('st["gmail_ui_switch"] = {')
    print("first gmail_ui_switch assign", idx, text[idx:idx+200])
    raise SystemExit("enqueue gmail_ui_switch marker not found")

new_enq = '''st["gmail_ui_switch"] = {
            "required": False,
            "target": first_mb,'''
text = text.replace(enq_marker, new_enq, 1)
# Also flip gmail_ui_switch_required in last_switch for enqueue
text = text.replace(
    '"reason": "test_per_account_start",\n            "gmail_ui_switch_required": True,',
    '"reason": "test_per_account_start",\n            "gmail_ui_switch_required": False,',
    1,
)
changes.append("enqueue-test: do not set gmail_ui_switch.required")

# 4) In poll when bound_sender != mailbox sets gmail_ui_switch — in multi-tab with prefer match this shouldn't run.
# Still: if prefer == bound_sender, skip setting switch (pinned path already used mailbox_entry).
# Change the claim-head switch block to not set required when prefer matches bound_sender.
old_sw_claim = '''                        s["gmail_ui_switch"] = {
                            "required": True, "target": bound_sender, "from": mailbox,
                            "at": now_iso(), "reason": "poll_claim_head_sender",'''
if old_sw_claim not in text:
    raise SystemExit("poll_claim_head_sender block not found")
new_sw_claim = '''                        # Multi-tab: if this tab already is bound_sender, no UI switch.
                        if prefer and prefer == bound_sender:
                            s["gmail_ui_switch"] = {
                                "required": False, "target": bound_sender, "from": prefer,
                                "at": now_iso(), "reason": "poll_claim_pinned_no_switch",
                                "note": "tab already on sender",
                            }
                        else:
                            s["gmail_ui_switch"] = {
                            "required": False, "target": bound_sender, "from": mailbox,
                            "at": now_iso(), "reason": "poll_claim_head_sender",'''
# Wait - if we set required False always, we need to fix the closing of the else. Simpler approach:
# Just set required False always for multi-tab (Jim: no switch wait).
new_sw_claim = '''                        s["gmail_ui_switch"] = {
                            "required": False, "target": bound_sender, "from": mailbox,
                            "at": now_iso(), "reason": "poll_claim_head_sender",'''
text = text.replace(old_sw_claim, new_sw_claim, 1)
changes.append("poll_claim_head_sender: never require gmail_ui_switch")

# Also the poll_job_sender block
old_sw_job = '''                        st["gmail_ui_switch"] = {
                            "required": True,
                            "target": job_sender,
                            "from": mailbox,
                            "at": now_iso(),
                            "reason": "poll_job_sender",'''
if old_sw_job in text:
    text = text.replace(old_sw_job, old_sw_job.replace('"required": True,', '"required": False,', 1), 1)
    changes.append("poll_job_sender: required False")
else:
    print("WARN: poll_job_sender block not exact")

# finish requeue keep switch required - change to False (multi-tab no avatar switch)
old_requeue_sw = '''                        sw["required"] = True
                        sender = str(job_snapshot.get("sender") or "").strip().lower()
                        if sender:
                            sw["target"] = sender
                        sw["at"] = now_iso()
                        sw["reason"] = "requeue_after_switch_fail"'''
if old_requeue_sw in text:
    text = text.replace(old_requeue_sw, '''                        # Multi-tab: do not re-arm avatar switch gate after pause.
                        sw["required"] = False
                        sender = str(job_snapshot.get("sender") or "").strip().lower()
                        if sender:
                            sw["target"] = sender
                        sw["at"] = now_iso()
                        sw["reason"] = "requeue_no_switch_gate"''', 1)
    changes.append("finish requeue: do not re-arm gmail_ui_switch")
else:
    print("WARN: requeue switch block not exact")

# apply_switch_to helper that sets required True - check line 554
# We'll leave account_switch path but clear on poll.

if text == orig:
    raise SystemExit("NO CHANGES MADE")

p.write_text(text, encoding="utf-8")
print("OK wrote local_queue.py")
for c in changes:
    print(" -", c)

# syntax check
import py_compile
py_compile.compile(str(p), doraise=True)
print("syntax OK")
