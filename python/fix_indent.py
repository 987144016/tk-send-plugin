# -*- coding: utf-8 -*-
from pathlib import Path
import py_compile
p = Path(r"C:\tk邀约\tiktok-outreach\packages\send-plugin\python\local_queue.py")
t = p.read_text(encoding="utf-8")
bad = "    accounts_n = len(list_accounts(c))\n        return {"
good = "    accounts_n = len(list_accounts(c))\n    return {"
if bad not in t:
    raise SystemExit("bad indent pattern not found")
p.write_text(t.replace(bad, good, 1), encoding="utf-8")
py_compile.compile(str(p), doraise=True)
print("syntax ok")
# quick import smoke
import sys
sys.path.insert(0, str(p.parent))
import local_queue as lq
c = lq.cfg()
print("accounts", len(lq.list_accounts(c)), "active", c.get("active_mailbox"))
sv = lq.settings_view(c)
print("settings accounts", len(sv.get("accounts") or []), "mailbox", sv.get("mailbox"))
mb, entry = lq.active_mailbox_entry(c)
print("template", entry.get("template_name"), "forbidden", entry.get("forbidden"))