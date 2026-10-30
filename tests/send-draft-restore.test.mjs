import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';

const require = createRequire(import.meta.url);
const {JSDOM} = require('C:/tk邀约/bd-mvp-dev/reports/sending-dom-fixture/node_modules/jsdom');
const source = readFileSync(new URL('../extension/page.js', import.meta.url), 'utf8');
const {gmailPage} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const dom = new JSDOM(`<!doctype html><html><body>
  <button aria-label="Google 账号： Sender (sender@gmail.com)"></button>
  <div role="dialog" data-gmw-job="job123">
    <input name="subjectbox" value="Invitation">
    <div class="minimized" contenteditable="true" role="textbox">Draft body</div>
    <button aria-label="最大化"></button>
  </div>
</body></html>`, {url: 'https://mail.google.com/mail/u/0/#inbox'});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.location = dom.window.location;
globalThis.MouseEvent = dom.window.MouseEvent;
globalThis.PointerEvent = dom.window.MouseEvent;
globalThis.HTMLTextAreaElement = dom.window.HTMLTextAreaElement;
globalThis.HTMLInputElement = dom.window.HTMLInputElement;
globalThis.Event = dom.window.Event;
dom.window.HTMLElement.prototype.getClientRects = function() {
  return this.classList.contains('minimized') ? [] : [{width: 10, height: 10}];
};
Object.defineProperty(dom.window.HTMLElement.prototype, 'innerText', {
  get() { return this.textContent; },
});
let clicked = 0;
document.querySelector('[aria-label="最大化"]').click = () => { clicked++; };
const result = await gmailPage('prepare', {
  sender: 'sender@gmail.com', recipient: 'creator@gmail.com',
  recipient_mode: 'bcc', recipients: ['creator@gmail.com'], job_id: 'job123',
});
assert.equal(result.waiting, true);
assert.equal(clicked, 1, 'the minimized draft is actually expanded');
assert.equal(result.click_selector, undefined, 'background need not execute an unused selector');
await gmailPage('prepare', {
  sender: 'sender@gmail.com', recipient: 'creator@gmail.com',
  recipient_mode: 'bcc', recipients: ['creator@gmail.com'], job_id: 'job123',
});
const exhausted = await gmailPage('prepare', {
  sender: 'sender@gmail.com', recipient: 'creator@gmail.com',
  recipient_mode: 'bcc', recipients: ['creator@gmail.com'], job_id: 'job123',
});
assert.equal(clicked, 2);
assert.equal(exhausted.error, 'gmail_draft_restore_failed',
  'a draft that cannot expand stops rather than waiting indefinitely');
const visibleBody = document.createElement('div');
visibleBody.setAttribute('contenteditable', 'true');
visibleBody.setAttribute('role', 'textbox');
visibleBody.textContent = 'Visible draft body';
document.querySelector('[role="dialog"]').appendChild(visibleBody);
const withVisibleBody = await gmailPage('prepare', {
  sender: 'sender@gmail.com', recipient: 'creator@gmail.com',
  recipient_mode: 'bcc', recipients: ['creator@gmail.com'], job_id: 'job123',
});
assert.equal(clicked, 2, 'an available visible body must not trigger another maximize click');
assert.notEqual(withVisibleBody.error, 'gmail_draft_restore_failed');
visibleBody.remove();
document.querySelector('[role="dialog"]').dataset.gmwRestoreTries = '2';
const close = document.createElement('button');
close.setAttribute('aria-label', 'Save & close');
let closed = 0;
close.click = () => { closed++; };
document.querySelector('[role="dialog"]').appendChild(close);
const recovered = await gmailPage('prepare', {
  sender: 'sender@gmail.com', recipient: 'creator@gmail.com',
  recipient_mode: 'bcc', recipients: ['creator@gmail.com'], job_id: 'job123',
});
assert.equal(closed, 1);
assert.equal(recovered.waiting, true);
const stillStuck = await gmailPage('prepare', {
  sender: 'sender@gmail.com', recipient: 'creator@gmail.com',
  recipient_mode: 'bcc', recipients: ['creator@gmail.com'], job_id: 'job123',
});
assert.equal(stillStuck.error, 'gmail_draft_restore_failed');
console.log('preparing a minimized matching draft clicks Gmail maximize once');
