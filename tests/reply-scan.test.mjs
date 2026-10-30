import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';

const require = createRequire(import.meta.url);
const {JSDOM} = require('C:/tk邀约/bd-mvp-dev/reports/sending-dom-fixture/node_modules/jsdom');
const source = readFileSync(new URL('../extension/page.js', import.meta.url), 'utf8');
const {gmailPage} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

const dom = new JSDOM(`<!doctype html><html><body>
  <button aria-label="Google 账号： Graham Thomas (wuxiaoyu28le@gmail.com)"></button>
  <main role="main"><table><tr class="zA" role="row">
    <td><span email="wuxiaoyu28le@gmail.com">我</span>, <span email="janaeelliott1003@gmail.com">Janae</span></td>
    <td><span class="y6" data-thread-id="#thread-a:r-371296337479953355" data-legacy-thread-id="1a1863ec24fa5b7d"
      data-legacy-last-non-draft-message-id="1a188d0a97c76728">tk邀约模版2</span>
      <span class="y2">Hello, my rate is $250 for a video with the ad code.</span></td>
  </tr></table></main>
</body></html>`, {url: 'https://mail.google.com/mail/u/3/#inbox'});
globalThis.document = dom.window.document;
globalThis.location = dom.window.location;
dom.window.HTMLElement.prototype.getClientRects = () => [{width: 10, height: 10}];
Object.defineProperty(dom.window.HTMLElement.prototype, 'innerText', {
  get() { return this.textContent; },
});

const scan = await gmailPage('scan_inbox', {sender: 'wuxiaoyu28le@gmail.com', recipient: ''});
assert.equal(scan.scanned, true);
assert.equal(scan.inbox_rows.length, 1);
assert.equal(scan.inbox_rows[0].from, 'janaeelliott1003@gmail.com');
assert.equal(scan.inbox_rows[0].unread, false);
assert.equal(scan.inbox_rows[0].message_id, '1a188d0a97c76728');
assert.equal(scan.inbox_rows[0].thread_url,
  'https://mail.google.com/mail/u/3/#all/thread-a:r-371296337479953355');
assert.equal(scan.replies_found, true);
console.log('reply scan recognizes a creator reply in a Gmail conversation row');

document.body.innerHTML = `<button aria-label="Google 账号： Graham Thomas (wuxiaoyu28le@gmail.com)"></button>
  <div data-legacy-message-id="1a188d0a97c76728">
    <span email="janaeelliott1003@gmail.com">Janae Elliott</span>
    <div class="a3s aiL">Hello, my rate is $250 for a video with the ad code.\nPlease let me know if you'd like to proceed.</div>
  </div>`;
const detail = await gmailPage('read_reply', {
  sender: 'wuxiaoyu28le@gmail.com', recipient: 'janaeelliott1003@gmail.com',
  message_id: '1a188d0a97c76728',
});
assert.equal(detail.verified, true);
assert.match(detail.body, /\$250 for a video with the ad code/);
console.log('reply detail reads the full message body from the verified Gmail message');
