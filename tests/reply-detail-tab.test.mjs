import assert from 'node:assert/strict';
import {withReplyDetailTab, cleanupReplyDetailTab} from '../extension/reply-detail-tab.mjs';

const records = {};
const tabs = [];
let creates = 0;
let failRemove = true;
const browser = {
  storage: {session: {
    async get(key) { return {[key]: records[key]}; },
    async set(patch) { Object.assign(records, patch); },
  }},
  tabs: {
    async create({url}) { const tab = {id: ++creates, url}; tabs.push(tab); return {...tab}; },
    async get(id) { const tab = tabs.find(t => t.id === id); if (!tab) throw Error('closed'); return {...tab}; },
    async update(id, {url}) { const tab = tabs.find(t => t.id === id); tab.url = url; return {...tab}; },
    async remove(id) {
      if (failRemove) { failRemove = false; throw Error('temporary close failure'); }
      tabs.splice(tabs.findIndex(t => t.id === id), 1);
    },
  },
};
const first = await withReplyDetailTab(browser, 'https://mail.google.com/mail/u/3/#all/one',
  async ({tabId, navigate}) => {
    await navigate('https://mail.google.com/mail/u/3/#all/two');
    return tabId;
  });
assert.equal(first, 1);
assert.equal(tabs.length, 1, 'failed close leaves one tracked temporary tab');
const second = await withReplyDetailTab(browser, 'https://mail.google.com/mail/u/3/#all/three',
  async ({tabId}) => tabId);
assert.equal(second, first, 'next scan reuses the same temporary tab');
assert.equal(creates, 1, 'cleanup failure cannot create a growing set of tabs');
assert.equal(tabs.length, 0, 'successful retry closes the temporary tab');
console.log('reply detail scans reuse and eventually close one tracked tab');

tabs.push({id: 77, url: 'https://mail.google.com/mail/u/3/#all/old', active: false});
records.replyDetailTab = {id: 77};
assert.equal(await cleanupReplyDetailTab(browser), true);
assert.equal(tabs.length, 0);
assert.equal(records.replyDetailTab, null);
console.log('worker startup closes a tracked leftover detail tab');
