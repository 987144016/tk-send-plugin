"""把飞书“发送状态”的英文存量值刷成中文，并统计各表现状。默认只统计。"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding='utf8')

cfg = json.loads((ROOT / 'config' / 'one-mailbox.json').read_text(encoding='utf-8'))
f = cfg['feishu']
exe = Path(os.environ.get('APPDATA', '')) / 'npm/node_modules/@larksuite/cli/bin/lark-cli.exe'
base = f['base_token']
profile = f.get('profile', 'tk-outreach-local')

MAP = {'sent': '已发送', 'bounced': '退信', 'replied': '已回复',
       'pending': '待发送', 'skip_no_email': '无邮箱跳过'}
apply = '--apply' in sys.argv
tables = [a for a in sys.argv[1:] if a.startswith('tbl')] or ['tblh3s12ihno8jEi', 'tblJu7x9zCtIsa9w']


def cli(args):
    return subprocess.run([str(exe), 'base', *args, '--base-token', base,
                           '--table-id', table, '--as', 'bot', '--profile', profile],
                          capture_output=True)


for table in tables:
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / 'r.ndjson'
        rows = []
        offset = 0
        while True:
            res = cli(['+record-list', '--format', 'ndjson', '--limit', '2000',
                       '--offset', str(offset), '--output', str(out), '--overwrite'])
            if not out.exists():
                print(table, 'list failed', (res.stdout or res.stderr).decode('utf-8', 'replace')[:200])
                break
            page = [json.loads(x) for x in out.read_text(encoding='utf-8').splitlines() if x.strip()]
            rows.extend(page)
            try:
                meta = json.JSONDecoder().raw_decode((res.stdout or b'{}').decode('utf-8', 'replace').lstrip())[0]
            except ValueError:
                meta = {}
            if not meta.get('has_more') or not page:
                break
            offset += len(page)

    counts = {}
    updates = {}
    for row in rows:
        value = row.get('发送状态')
        if isinstance(value, list):
            value = value[0] if value else ''
        value = str(value or '').strip()
        counts[value or '(空)'] = counts.get(value or '(空)', 0) + 1
        if value in MAP:
            updates.setdefault(MAP[value], []).append(row['record_id'])

    print(table, 'rows', len(rows), counts)
    if not apply or not updates:
        continue
    for target, ids in updates.items():
        for start in range(0, len(ids), 100):
            chunk = ids[start:start + 100]
            payload = {'update_records': {rid: {'发送状态': [target]} for rid in chunk}}
            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / 'p.json'
                p.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
                r = subprocess.run([str(exe), 'base', '+record-batch-update',
                                    '--base-token', base, '--table-id', table,
                                    '--as', 'bot', '--profile', profile, '--json', '@' + str(p)],
                                   capture_output=True)
                print(' ', target, len(chunk), 'rc', r.returncode,
                      (r.stdout or r.stderr).decode('utf-8', 'replace')[:160].replace('\n', ' '))
