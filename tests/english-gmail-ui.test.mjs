import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';

const require = createRequire(import.meta.url);
const {JSDOM} = require('C:/tk邀约/bd-mvp-dev/reports/sending-dom-fixture/node_modules/jsdom');
const source = readFileSync(new URL('../extension/page.js', import.meta.url), 'utf8');
const {gmailPage} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const dom = new JSDOM(`<!doctype html><html><body>
  <button aria-label="Google Account: Sender (sender@gmail.com)"></button>
  <button id="compose">Compose</button>
  <button role="button" aria-label="Mark as unread"></button>
  <main role="main"><table><tr class="zA"><td>
    <span email="creator@gmail.com"></span>
    <span data-legacy-last-non-draft-message-id="1a188d0a97c76728" data-legacy-thread-id="1a188d0a97c76720"></span>
    <span class="y6">Re: Invitation</span><span class="y2">My rate is $250</span>
  </td></tr></table></main>
</body></html>`, {url: 'https://mail.google.com/mail/u/0/#inbox'});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.location = dom.window.location;
globalThis.HTMLTextAreaElement = dom.window.HTMLTextAreaElement;
globalThis.HTMLInputElement = dom.window.HTMLInputElement;
dom.window.HTMLElement.prototype.getClientRects = function() { return [{width: 10, height: 10}]; };
Object.defineProperty(dom.window.HTMLElement.prototype, 'innerText', {
  get() { return this.textContent; },
});
let composeClicks = 0;
document.getElementById('compose').click = () => { composeClicks++; };
const job = {sender: 'sender@gmail.com', recipient: 'creator@gmail.com', job_id: 'job1'};
const prepare = await gmailPage('prepare', job);
assert.equal(prepare.waiting, true);
assert.equal(composeClicks, 1);
const scan = await gmailPage('scan_inbox', job);
assert.equal(scan.scanned, true);
assert.equal(scan.replies[0].from, 'creator@gmail.com');
assert.equal(scan.replies[0].message_id, '1a188d0a97c76728');
const unread = await gmailPage('restore_unread', job);
assert.equal(unread.requested, true);
console.log('English Gmail Compose, inbox reply and Mark as unread are recognized');
