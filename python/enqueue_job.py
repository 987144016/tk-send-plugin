#!/usr/bin/env python3
"""Enqueue ONE job for the standalone send-plugin. Default is dry-run (writes preview only).

Never mass-sends. Requires --confirm-send to place a pending job, and config armed+template.
"""
from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config" / "one-mailbox.json"
JOBS = ROOT / "data" / "jobs"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--to", required=True, help="recipient email")
    p.add_argument("--creator-name", default="")
    p.add_argument("--confirm-send", action="store_true", help="actually write pending-*.json")
    p.add_argument("--inline-images", type=int, default=2)
    args = p.parse_args()
    c = json.loads(CFG.read_text(encoding="utf-8"))
    mailbox = (c.get("active_mailbox") or c.get("mailbox") or "").lower()
    entry = (c.get("mailboxes") or {}).get(mailbox) or {}
    template = (entry.get("template_name") or c.get("template_name") or "").strip()
    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "sender": mailbox,
        "recipient": args.to.strip().lower(),
        "creator_name": args.creator_name,
        "subject": c.get("subject") or "Tiktok paid collaboration from PoseFree Apparel",
        "gmail_template_name": template,
        "marker": "TK-" + job_id[:12],
        "inline_image_count": args.inline_images,
        "recipient_mode": "to",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    print(json.dumps({"preview": job, "armed": c.get("armed"), "template": template}, ensure_ascii=False, indent=2))
    if not args.confirm_send:
        print("dry-run only (pass --confirm-send to enqueue). No mass-send.")
        return
    if not c.get("armed") or not template:
        raise SystemExit("refusing enqueue: armed=false or template_name empty")
    if template in (c.get("forbidden_templates") or []):
        raise SystemExit(f"refusing enqueue: forbidden template {template}")
    JOBS.mkdir(parents=True, exist_ok=True)
    path = JOBS / f"pending-{job_id}.json"
    path.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("enqueued", path)


if __name__ == "__main__":
    main()
