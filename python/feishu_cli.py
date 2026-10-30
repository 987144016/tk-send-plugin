"""Run Feishu Base commands using a private file, relay, or local CLI."""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEAM_RELAY = ROOT / "config" / "team-relay.json"
try:
    from feishu_direct import enabled as direct_enabled, list_records, get_record, batch_update
except ImportError:  # pragma: no cover
    from python.feishu_direct import enabled as direct_enabled, list_records, get_record, batch_update  # type: ignore


def relay_enabled() -> bool:
    return TEAM_RELAY.is_file()


def run_lark(args: list[str], *, cwd=None, timeout=60, **kwargs) -> subprocess.CompletedProcess:
    if direct_enabled():
        command = next((x for x in args if x in ("+record-list", "+record-get", "+record-batch-update")), "")
        def value(flag: str) -> str:
            i = args.index(flag) if flag in args else -1
            return str(args[i + 1]) if i >= 0 and i + 1 < len(args) else ""
        base, table = value("--base-token"), value("--table-id")
        output = value("--output")
        if command == "+record-list":
            rows = list_records(base, table)
            meta = {"has_more": False, "count": len(rows)}
        elif command == "+record-get":
            rows = [get_record(base, table, value("--record-id"))]
            meta = {"ok": True}
        elif command == "+record-batch-update":
            source = value("--json")
            if not source.startswith("@"):
                raise ValueError("missing update payload")
            update = json.loads(Path(source[1:]).read_text(encoding="utf-8"))
            data = batch_update(base, table, update)
            rows = []
            meta = {"ok": True, "data": data}
        else:
            raise ValueError("unsupported Feishu command")
        if output:
            target = Path(cwd or os.getcwd()) / output
            target.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, json.dumps(meta, ensure_ascii=False).encode("utf-8"), b"")
    if not relay_enabled():
        return subprocess.run(args, cwd=cwd, timeout=timeout, **kwargs)
    config = json.loads(TEAM_RELAY.read_text(encoding="utf-8-sig"))
    command = next((x for x in args if x in ("+record-list", "+record-get", "+record-batch-update")), "")
    if not command:
        raise ValueError("unsupported Feishu command")

    def value(flag: str) -> str:
        i = args.index(flag) if flag in args else -1
        return str(args[i + 1]) if i >= 0 and i + 1 < len(args) else ""

    payload = {"op": "feishu_cli", "command": command,
               "base_token": value("--base-token"), "table_id": value("--table-id"),
               "record_id": value("--record-id"), "field_id": value("--field-id"),
               "offset": int(value("--offset") or 0), "limit": int(value("--limit") or 2000)}
    if command == "+record-batch-update":
        source = value("--json")
        if not source.startswith("@"):
            raise ValueError("missing update payload")
        payload["update"] = json.loads(Path(source[1:]).read_text(encoding="utf-8"))
    request = urllib.request.Request(config["relay_url"].rstrip("/") + "/feishu",
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        {"Authorization": "Bearer " + config["client_key"],
         "Content-Type": "application/json",
         "User-Agent": "TK-Send-Plugin/0.3.21"}, method="POST")
    with urllib.request.urlopen(request, timeout=min(float(timeout), 85)) as response:
        result = json.load(response)
    if not result.get("ok"):
        raise RuntimeError("center Feishu request failed: " + str(result.get("error") or "unknown"))
    output = value("--output")
    if output:
        target = Path(cwd or os.getcwd()) / output
        target.write_text(result.get("output") or "", encoding="utf-8")
    stdout = (result.get("stdout") or "").encode("utf-8")
    return subprocess.CompletedProcess(args, int(result.get("returncode") or 0), stdout, b"")
