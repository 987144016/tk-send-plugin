import assert from 'node:assert/strict';
import {ensureGmailAccountTabs} from '../extension/gmail-tabs.mjs';

function browser(initial, onSwitch) {
  let nextId = 10;
  const tabs = initial.map(tab => ({...tab}));
  const data = {};
  const chrome = {
    tabs: {
      async query() { return tabs.filter(t => t.url.startsWith('https://mail.google.com/')).map(t => ({...t})); },
      async get(id) { const tab = tabs.find(t => t.id === id); if (!tab) throw Error('tab_closed'); return {...tab}; },
      async create({url}) {
        const tab = {id: nextId++, url, status: 'complete',
          mailbox: initial[0]?.mailbox || 'sender@gmail.com'};
        tabs.push(tab);
        return {...tab};
      },
      async remove(id) { tabs.splice(tabs.findIndex(t => t.id === id), 1); },
    },
    storage: {local: {
      async get(key) { return {[key]: data[key]}; },
      async set(patch) { Object.assign(data, patch); },
    }},
  };
  const readMailbox = async id => tabs.find(t => t.id === id)?.mailbox || '';
  const switchAccount = async (id, target) => onSwitch(tabs, id, target, nextId++);
  return {chrome, tabs, data, readMailbox, switchAccount};
}

const accounts = ['sender@gmail.com', 'second@gmail.com'];
const existing = browser([
  {id: 1, url: 'https://mail.google.com/mail/u/0/#inbox', status: 'complete', mailbox: 'sender@gmail.com'},
  {id: 2, url: 'https://mail.google.com/mail/u/1/#inbox', status: 'complete', mailbox: 'second@gmail.com'},
], () => { throw Error('account switch must not run'); });
assert.equal((await ensureGmailAccountTabs(existing.chrome, accounts,
  existing.readMailbox, existing.switchAccount)).status, 'ready');
assert.equal(existing.tabs.length, 2);
console.log('existing account tabs remain untouched');

const b = browser([{id: 1, url: 'https://mail.google.com/mail/u/0/#inbox',
  status: 'complete', mailbox: 'sender@gmail.com'}], (tabs, id, target) => {
  tabs.find(t => t.id === id).mailbox = target;
  return {waiting: true, account_row_clicked: true};
});
await ensureGmailAccountTabs(b.chrome, accounts, b.readMailbox, b.switchAccount);
assert.equal(b.tabs.length, 2, 'missing account opens one Gmail tab');
await ensureGmailAccountTabs(b.chrome, accounts, b.readMailbox, b.switchAccount);
await ensureGmailAccountTabs(b.chrome, accounts, b.readMailbox, b.switchAccount);
assert.deepEqual(b.tabs.map(t => t.mailbox).sort(), [...accounts].sort());
assert.equal(b.tabs.length, accounts.length, 'switching never leaves surplus tabs');
await ensureGmailAccountTabs(b.chrome, accounts, b.readMailbox, b.switchAccount);
assert.equal(b.tabs.length, accounts.length, 'a complete setup opens nothing on later checks');
console.log('one existing Gmail tab expands to exactly one tab per configured account');

const blank = browser([{id: 1, url: 'https://mail.google.com/mail/u/0/#inbox',
  status: 'complete', mailbox: 'sender@gmail.com'}], (tabs, _id, target, newId) => {
  tabs.push({id: newId, url: 'https://mail.google.com/mail/u/1/#inbox',
    status: 'complete', mailbox: target});
  return {waiting: true, account_row_clicked: true};
});
await ensureGmailAccountTabs(blank.chrome, accounts, blank.readMailbox, blank.switchAccount);
await ensureGmailAccountTabs(blank.chrome, accounts, blank.readMailbox, blank.switchAccount);
assert.equal(blank.tabs.length, 3, 'Gmail account link may briefly open another tab');
await ensureGmailAccountTabs(blank.chrome, accounts, blank.readMailbox, blank.switchAccount);
assert.equal(blank.tabs.length, 2, 'the temporary plugin tab is closed after target account is verified');
assert.deepEqual(blank.tabs.map(t => t.mailbox).sort(), [...accounts].sort());
console.log('a Gmail account link that opens a tab still finishes at account count');

const stalled = browser([{id: 1, url: 'https://mail.google.com/mail/u/0/#inbox',
  status: 'complete', mailbox: 'sender@gmail.com'}], () =>
  ({waiting: true, account_row_clicked: true}));
await ensureGmailAccountTabs(stalled.chrome, accounts, stalled.readMailbox, stalled.switchAccount);
await ensureGmailAccountTabs(stalled.chrome, accounts, stalled.readMailbox, stalled.switchAccount);
stalled.data.gmailAccountProvision.rowClickedAt = Date.now() - 16000;
await ensureGmailAccountTabs(stalled.chrome, accounts, stalled.readMailbox, stalled.switchAccount);
stalled.data.gmailAccountProvision.retryAt = 0;
await ensureGmailAccountTabs(stalled.chrome, accounts, stalled.readMailbox, stalled.switchAccount);
stalled.data.gmailAccountProvision.rowClickedAt = Date.now() - 16000;
const stop = await ensureGmailAccountTabs(stalled.chrome, accounts,
  stalled.readMailbox, stalled.switchAccount);
assert.equal(stop.status, 'attention');
assert.equal(stalled.tabs.length, accounts.length);
console.log('an unconfirmed account switch stops retrying without growing tabs');

const login = browser([{id: 1, url: 'https://mail.google.com/mail/u/0/#inbox',
  status: 'complete', mailbox: 'sender@gmail.com'}], () =>
  ({waiting: true}));
await ensureGmailAccountTabs(login.chrome, accounts, login.readMailbox, login.switchAccount);
login.tabs[1].url = 'https://accounts.google.com/signin/';
assert.equal((await ensureGmailAccountTabs(login.chrome, accounts,
  login.readMailbox, login.switchAccount)).status, 'attention');
await ensureGmailAccountTabs(login.chrome, accounts, login.readMailbox, login.switchAccount);
assert.equal(login.tabs.length, 2, 'a login redirect does not create endless tabs');
console.log('login redirects stop tab creation until the user signs in');
