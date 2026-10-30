import assert from 'node:assert/strict';
import worker from './worker.mjs';

let online = false;
const env = {
  CLIENT_KEY: 'private_client_key', HOST_KEY: 'private_host_key',
  RELAY: {
    idFromName: name => name,
    get: () => ({fetch: async request => {
      const path = new URL(request.url).pathname;
      if (path === '/status') return Response.json({ok: true, center_online: online});
      return Response.json(online ? {ok: true, accepted: true} :
        {ok: false, error: 'center_offline'}, {status: online ? 200 : 503});
    }}),
  },
};

const denied = await worker.fetch(new Request('https://relay.example/events', {
  method: 'POST', body: '{}',
}), env);
assert.equal(denied.status, 401);
const headers = {authorization: 'Bearer private_client_key'};
const offline = await worker.fetch(new Request('https://relay.example/events', {
  method: 'POST', headers, body: '{}',
}), env);
assert.equal(offline.status, 503);
online = true;
const accepted = await worker.fetch(new Request('https://relay.example/events', {
  method: 'POST', headers, body: '{}',
}), env);
assert.equal(accepted.status, 200);
assert.equal((await accepted.json()).accepted, true);
console.log('relay rejects unknown clients and reports center-offline without accepting events');
