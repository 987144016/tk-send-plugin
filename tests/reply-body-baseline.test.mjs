import assert from 'node:assert/strict';
import {newReplyBodyCandidates, markReplyBodyRead} from '../extension/reply-body-baseline.mjs';

const saved = {};
const storage = {
  async get(keys) { return Object.fromEntries(keys.map(k => [k, saved[k]])); },
  async set(patch) { Object.assign(saved, patch); },
};
const mailbox = 'sender@gmail.com';
const old = {message_id: '1a182b44a7b872ea', from: 'old@gmail.com'};
const next = {message_id: '1a182b44a7b872eb', from: 'new@gmail.com'};
assert.deepEqual(await newReplyBodyCandidates(storage, mailbox, [old]), [],
  'first scan records existing inbox mail without opening a detail tab');
assert.deepEqual(await newReplyBodyCandidates(storage, mailbox, [old, next]), [next],
  'only a newly arrived reply needs full body reading');
await markReplyBodyRead(storage, mailbox, next.message_id);
assert.deepEqual(await newReplyBodyCandidates(storage, mailbox, [old, next]), [],
  'verified messages are not reopened on every scan');
console.log('reply detail reads only new Gmail messages after first inbox baseline');
