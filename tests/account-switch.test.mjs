import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';

const require = createRequire(import.meta.url);
const {JSDOM} = require('C:/tk邀约/bd-mvp-dev/reports/sending-dom-fixture/node_modules/jsdom');
const source = readFileSync(new URL('../extension/page.js', import.meta.url), 'utf8');
const {gmailPage} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const dom = new JSDOM(`<!doctype html><html><body>
  <button aria-label="Google 账号： Sender (sender@gmail.com)"></button>
  <a data-email="second@gmail.com" target="_blank" href="https://mail.google.com/mail/u/1/">second@gmail.com</a>
</body></html>`, {url: 'https://mail.google.com/mail/u/0/#inbox'});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.location = dom.window.location;
globalThis.MouseEvent = dom.window.MouseEvent;
globalThis.PointerEvent = dom.window.MouseEvent;
globalThis.HTMLTextAreaElement = dom.window.HTMLTextAreaElement;
globalThis.HTMLInputElement = dom.window.HTMLInputElement;
globalThis.Event = dom.window.Event;
dom.window.HTMLElement.prototype.getClientRects = () => [{width: 10, height: 10}];
Object.defineProperty(dom.window.HTMLElement.prototype, 'innerText', {
  get() { return this.textContent; },
});
const link = document.querySelector('a[data-email]');
let clicks = 0;
link.click = () => { clicks++; };
const result = await gmailPage('switch_account', {sender: 'second@gmail.com'});
assert.equal(result.account_row_clicked, true);
assert.equal(clicks, 1, 'one account selection causes one click');
assert.equal(link.target, '_self', 'selected account replaces the staging tab');
console.log('account switching clicks the target email once in the staging tab');
