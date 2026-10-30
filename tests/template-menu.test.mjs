import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';

const require = createRequire(import.meta.url);
const {JSDOM} = require('C:/tk邀约/bd-mvp-dev/reports/sending-dom-fixture/node_modules/jsdom');
const source = readFileSync(new URL('../extension/page.js', import.meta.url), 'utf8');
const {gmailPage} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

const template = 'Tiktok paid collaboration from PoseFree Apparel';
const dom = new JSDOM(`<!doctype html><html><body>
  <div aria-label="Google Account: sender@gmail.com"></div>
  <div role="dialog" data-gmw-job="job1">
    <input name="subjectbox" value="Subject">
    <input name="to"><span email="creator@gmail.com"></span>
    <div role="textbox" contenteditable="true"></div>
  </div>
  <div role="menuitem" title="${template}">Tiktok paid collaboration fro...</div>
</body></html>`, {url: 'https://mail.google.com/mail/u/0/#inbox'});

globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.location = dom.window.location;
globalThis.MouseEvent = dom.window.MouseEvent;
globalThis.HTMLTextAreaElement = dom.window.HTMLTextAreaElement;
globalThis.HTMLInputElement = dom.window.HTMLInputElement;
dom.window.HTMLElement.prototype.getClientRects = function() { return [{width: 20, height: 20}]; };
Object.defineProperty(dom.window.HTMLElement.prototype, 'innerText', {get() { return this.textContent; }});

const item = document.querySelector('[role="menuitem"]');
const compose = document.querySelector('[role="dialog"]');
const job = {job_id: 'job1', sender: 'sender@gmail.com', recipient: 'creator@gmail.com',
  subject: 'Subject', gmail_template_name: template};
let clicks = 0;
item.click = () => { clicks++; };

const titled = await gmailPage('prepare', job);
assert.equal(titled.waiting, true);
assert.equal(clicks, 1, 'full title selects the saved template');
assert.equal(compose.dataset.gmwTemplateRequested, template);

delete compose.dataset.gmwTemplateRequested;
item.removeAttribute('title');
const shortened = await gmailPage('prepare', job);
assert.equal(shortened.waiting, true);
assert.equal(clicks, 2, 'a unique truncated menu label selects the full template name');

delete compose.dataset.gmwTemplateRequested;
const duplicate = item.cloneNode(true);
document.body.appendChild(duplicate);
const ambiguous = await gmailPage('prepare', job);
assert.equal(ambiguous.error, 'ambiguous_saved_template', 'same truncated prefix never guesses');

console.log('Gmail saved template menu accepts a unique ellipsized long name');
