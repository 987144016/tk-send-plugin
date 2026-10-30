import assert from 'node:assert/strict';
import {captureReplyEvents, flushReplyEvents} from '../extension/reply-relay.mjs';

const state = {};
const storage = {
  async get(keys) { return Object.fromEntries(keys.map(k => [k, state[k]])); },
  async set(values) { Object.assign(state, values); },
};
const reply = {from: 'creator@gmail.com', message_id: '1a188d0a97c76728',
  at: '2026年10月30日周五 01:58', body: 'My rate is $250.', body_verified: true};
assert.deepEqual(await captureReplyEvents(storage, 'sender@gmail.com', {replies: [reply]}),
  {queued: 0, baseline: true});
const newer = {...reply, message_id: '1a188d0a97c76729'};
assert.deepEqual(await captureReplyEvents(storage, 'sender@gmail.com', {replies: [newer]}),
  {queued: 1, baseline: false});
const offline = async () => ({ok: false, status: 503, json: async () => ({error: 'center_offline'})});
assert.equal((await flushReplyEvents(storage, 'https://relay.example', 'key', offline)).status, 'pending');
assert.equal(Object.keys(state.replyRelayOutbox).length, 1);
for (const item of Object.values(state.replyRelayOutbox)) item.next_at = 0;
const accepted = async () => ({ok: true, status: 200,
  json: async () => ({ok: true, status: 'sent', feishu_message_id: 'om_confirmed'})});
assert.equal((await flushReplyEvents(storage, 'https://relay.example', 'key', accepted)).status, 'confirmed');
assert.equal(Object.keys(state.replyRelayOutbox).length, 0);
console.log('reply outbox keeps offline events and clears only after Feishu receipt');
