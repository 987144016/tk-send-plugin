#!/usr/bin/env python3
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config" / "one-mailbox.json"

def main():
    c = json.loads(CFG.read_text(encoding="utf-8"))
    print("config.armed =", c.get("armed"))
    print("active_mailbox =", c.get("active_mailbox") or c.get("mailbox"))
    print("mailboxes map:")
    for k, v in (c.get("mailboxes") or {}).items():
        print(f"  {k}: template={v.get('template_name')!r} role={v.get('role')} armed={v.get('armed')}")
    host = (c.get("listen") or {}).get("host", "127.0.0.1")
    port = (c.get("listen") or {}).get("port", 8767)
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=3) as r:
            print("health:", r.read().decode())
    except Exception as e:
        print("health: offline —", e)
    jobs = ROOT / "data" / "jobs"
    pending = list(jobs.glob("pending-*.json")) if jobs.is_dir() else []
    print("pending jobs:", len(pending))

if __name__ == "__main__":
    main()
