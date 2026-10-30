"""Feishu OpenAPI client for the separately shared team-feishu.json file."""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEAM_FILE = ROOT / 'extension' / 'team-feishu.json'
API = 'https://open.feishu.cn/open-apis'
_token_lock = threading.Lock()
_token = ''
_expires_at = 0.0


def enabled() -> bool:
    return TEAM_FILE.is_file()


def config() -> dict:
    if not enabled():
        raise FileNotFoundError('team-feishu.json is missing beside extension/manifest.json')
    value = json.loads(TEAM_FILE.read_text(encoding='utf-8-sig'))
    if not all(str(value.get(k) or '').strip() for k in ('app_id', 'app_secret', 'base_token')):
        raise ValueError('team-feishu.json needs app_id, app_secret and base_token')
    return value


def _post_json(url: str, body: dict, *, token: str = '', timeout: float = 30) -> dict:
    headers = {'Content-Type': 'application/json', 'User-Agent': 'TK-Send-Plugin/0.3.22'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(url,
        json.dumps(body, ensure_ascii=False).encode('utf-8'), headers, method='POST')
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.load(response)
    if result.get('code') != 0:
        raise RuntimeError('Feishu API error %s: %s' % (result.get('code'), str(result.get('msg') or '')[:160]))
    return result


def _tenant_token() -> str:
    global _token, _expires_at
    if _token and time.time() < _expires_at:
        return _token
    with _token_lock:
        if _token and time.time() < _expires_at:
            return _token
        c = config()
        result = _post_json(API + '/auth/v3/tenant_access_token/internal',
            {'app_id': c['app_id'], 'app_secret': c['app_secret']}, timeout=20)
        token = str(result.get('tenant_access_token') or '')
        if not token:
            raise RuntimeError('Feishu token missing')
        _token, _expires_at = token, time.time() + max(60, int(result.get('expire') or 7200) - 120)
        return _token


def _api(method: str, path: str, *, data: dict | None = None,
         params: dict | None = None, timeout: float = 30) -> dict:
    url = API + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    payload = None if data is None else json.dumps(data, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(url, payload, {
        'Authorization': 'Bearer ' + _tenant_token(),
        'Content-Type': 'application/json',
        'User-Agent': 'TK-Send-Plugin/0.3.22',
    }, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.load(response)
    if result.get('code') != 0:
        raise RuntimeError('Feishu API error %s: %s' % (result.get('code'), str(result.get('msg') or '')[:160]))
    return result.get('data') or {}


def _record_path(base_token: str, table_id: str) -> str:
    return '/bitable/v1/apps/%s/tables/%s/records' % (
        urllib.parse.quote(base_token, safe=''), urllib.parse.quote(table_id, safe=''))


def list_records(base_token: str, table_id: str) -> list[dict]:
    rows = []
    page_token = ''
    while True:
        params = {'page_size': 200}
        if page_token:
            params['page_token'] = page_token
        data = _api('GET', _record_path(base_token, table_id), params=params)
        rows.extend({'record_id': item.get('record_id') or item.get('id'),
                     **dict(item.get('fields') or {})} for item in (data.get('items') or []))
        if not data.get('has_more'):
            return rows
        nxt = str(data.get('page_token') or '')
        if not nxt or nxt == page_token:
            raise RuntimeError('Feishu list pagination stopped without advancing')
        page_token = nxt


def get_record(base_token: str, table_id: str, record_id: str) -> dict:
    data = _api('GET', _record_path(base_token, table_id) + '/' +
                urllib.parse.quote(record_id, safe=''))
    item = data.get('record') or {}
    return {'record_id': item.get('record_id') or record_id, **dict(item.get('fields') or {})}


def batch_update(base_token: str, table_id: str, updates: dict) -> dict:
    records = []
    for record_id, fields in (updates.get('update_records') or {}).items():
        normalized = dict(fields)
        if isinstance(normalized.get('发送状态'), list):
            normalized['发送状态'] = normalized['发送状态'][0]
        records.append({'record_id': record_id, 'fields': normalized})
    if not records:
        raise ValueError('No records to update')
    return _api('POST', _record_path(base_token, table_id) + '/batch_update',
                data={'records': records})


def send_group_text(chat_id: str, body: str, key: str) -> dict:
    data = _api('POST', '/im/v1/messages', params={'receive_id_type': 'chat_id'},
        data={'receive_id': chat_id, 'msg_type': 'text',
              'content': json.dumps({'text': body}, ensure_ascii=False),
              'uuid': str(uuid.uuid5(uuid.NAMESPACE_URL, key))})
    if not data.get('message_id') or data.get('chat_id') != chat_id:
        raise RuntimeError('Feishu group receipt not confirmed')
    return {'ok': True, 'data': {'message_id': data['message_id'], 'chat_id': chat_id}}
