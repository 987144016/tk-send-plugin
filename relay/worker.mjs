const respond = (body, status = 200) => Response.json(body, {
  status,
  headers: {'cache-control': 'no-store', 'access-control-allow-origin': '*'},
});

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (request.method === 'OPTIONS') return new Response(null, {status: 204, headers: {
      'access-control-allow-origin': '*',
      'access-control-allow-methods': 'GET, POST, OPTIONS',
      'access-control-allow-headers': 'Authorization, Content-Type',
    }});
    const relay = env.RELAY.get(env.RELAY.idFromName('center'));
    if (path === '/health' && request.method === 'GET')
      return relay.fetch(new Request('https://relay.internal/status'));
    if (path === '/host') {
      if (!env.HOST_KEY || request.headers.get('authorization') !== 'Bearer ' + env.HOST_KEY)
        return respond({ok: false, error: 'forbidden'}, 403);
      return relay.fetch(request);
    }
    if (!['/events', '/feishu'].includes(path) || request.method !== 'POST')
      return respond({ok: false, error: 'not_found'}, 404);
    if (!env.CLIENT_KEY || request.headers.get('authorization') !== 'Bearer ' + env.CLIENT_KEY)
      return respond({ok: false, error: 'unauthorized'}, 401);
    const body = await request.text();
    if (new TextEncoder().encode(body).length > 128 * 1024)
      return respond({ok: false, error: 'too_large'}, 413);
    return relay.fetch(new Request('https://relay.internal' + path, {
      method: 'POST', headers: {'content-type': 'application/json'}, body,
    }));
  },
};

export class ReplyRelay {
  constructor(ctx, env) { this.ctx = ctx; this.env = env; this.pending = new Map(); }

  async fetch(request) {
    const path = new URL(request.url).pathname;
    const host = this.ctx.getWebSockets('host').find(ws => ws.readyState === WebSocket.OPEN);
    if (path === '/status') return respond({ok: true, center_online: !!host});
    if (path === '/host') {
      if (request.headers.get('authorization') !== 'Bearer ' + this.env.HOST_KEY ||
          request.headers.get('upgrade') !== 'websocket') return respond({ok: false}, 403);
      for (const old of this.ctx.getWebSockets('host')) old.close(1000, 'Reconnected');
      const pair = new WebSocketPair();
      this.ctx.acceptWebSocket(pair[1], ['host']);
      return new Response(null, {status: 101, webSocket: pair[0]});
    }
    if (!['/events', '/feishu'].includes(path)) return respond({ok: false}, 404);
    if (!host) return respond({ok: false, error: 'center_offline'}, 503);
    if (this.pending.size >= 8) return respond({ok: false, error: 'center_busy'}, 429);
    const body = await request.text();
    const id = crypto.randomUUID();
    return new Promise(resolve => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        resolve(respond({ok: false, error: 'center_timeout'}, 504));
      }, 90000);
      this.pending.set(id, {resolve, timer});
      try { host.send(JSON.stringify({id, body, kind: path === '/feishu' ? 'feishu_cli' : 'reply'})); }
      catch {
        clearTimeout(timer);
        this.pending.delete(id);
        resolve(respond({ok: false, error: 'center_disconnected'}, 503));
      }
    });
  }

  webSocketMessage(_ws, message) {
    let data;
    try { data = JSON.parse(message); } catch { return; }
    if (data.kind === 'heartbeat') return;
    const item = this.pending.get(data.id);
    if (!item) return;
    clearTimeout(item.timer);
    this.pending.delete(data.id);
    item.resolve(respond(data.result || {ok: false, error: 'empty_center_result'}, data.status || 200));
  }

  webSocketClose(ws) {
    try { ws.close(1000, 'Disconnected'); } catch {}
    for (const item of this.pending.values()) {
      clearTimeout(item.timer);
      item.resolve(respond({ok: false, error: 'center_disconnected'}, 503));
    }
    this.pending.clear();
  }
  webSocketError(ws) { this.webSocketClose(ws); }
}
