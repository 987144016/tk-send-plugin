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

item.remove(); duplicate.remove();
delete compose.dataset.gmwTemplateRequested;
const more = document.createElement('button');
more.setAttribute('aria-label', '更多选项');
compose.appendChild(more);
let moreClicks = 0;
more.click = () => { moreClicks++; };
const menu = document.createElement('div');
menu.setAttribute('role', 'menu');
menu.innerHTML = '<div class="J-N"><div class="J-N-Jz">插入模板</div></div><div class="J-N"><div class="J-N-Jz">tk邀约模板2</div></div>';
document.body.appendChild(menu);
const savedTemplate = menu.lastElementChild;
let savedClicks = 0;
savedTemplate.click = () => { savedClicks++; };
const fromRealMenu = await gmailPage('prepare', {...job, gmail_template_name: 'tk邀约模板2'});
assert.equal(fromRealMenu.waiting, true);
assert.equal(savedClicks, 1, 'visible Gmail template without role=menuitem is selected');
assert.equal(moreClicks, 0, 'an already open menu is not reopened');
delete compose.dataset.gmwTemplateRequested;
const missing = await gmailPage('prepare', {...job, gmail_template_name: 'missing template'});
assert.equal(missing.error, 'gmail_template_not_found', 'an open saved-template list reports an absent name');
assert.equal(moreClicks, 0, 'missing template does not reopen the menu');

console.log('Gmail saved template menu accepts a unique ellipsized long name');
