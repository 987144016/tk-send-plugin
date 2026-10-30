import {gmailPage} from './page.js';
import {ORIGIN, accountURL, canClick, needsReconciliation, receiptMatches} from './protocol.js';
import {REPLY_RELAY_URL, REPLY_RELAY_KEY} from './relay-config.js';
import {captureReplyEvents, flushReplyEvents} from './reply-relay.mjs';
import {ensureGmailAccountTabs} from './gmail-tabs.mjs';
import {withReplyDetailTab, cleanupReplyDetailTab} from './reply-detail-tab.mjs';
import {newReplyBodyCandidates, markReplyBodyRead} from './reply-body-baseline.mjs';

const NATIVE_HOST = 'com.tk.outreach.sendqueue';
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
let lastNativeEnsureAt = 0;

async function probeQueueHealth() {
  const r = await fetch('http://127.0.0.1:8767/api/health', {signal: AbortSignal.timeout(2500)});
  const j = await r.json();
  if (!r.ok || !j.ok) throw Error(j.detail || 'health fail');
  return j;
}

function nativeStartQueue() {
  return new Promise((resolve) => {
    let port;
    try { port = chrome.runtime.connectNative(NATIVE_HOST); }
    catch (e) { resolve({ok: false, error: String(e)}); return; }
    let done = false;
    const finish = (o) => { if (done) return; done = true; try { port.disconnect(); } catch {} resolve(o); };
    const t = setTimeout(() => finish({ok: false, error: 'timeout'}), 70000);
    port.onMessage.addListener((msg) => { clearTimeout(t); finish(msg || {ok: true}); });
    port.onDisconnect.addListener(() => {
      clearTimeout(t);
      finish({ok: false, error: (chrome.runtime.lastError && chrome.runtime.lastError.message) || 'disconnect'});
    });
    try { port.postMessage({cmd: 'start'}); }
    catch (e) { clearTimeout(t); finish({ok: false, error: String(e)}); }
  });
}

async function ensureQueueRunning() {
  try { return await probeQueueHealth(); }
  catch {}
  const now = Date.now();
  if (now - lastNativeEnsureAt < 15000) return null;
  lastNativeEnsureAt = now;
  const n = await nativeStartQueue();
  if (!n || !n.ok) return null;
  for (let i = 0; i < 20; i++) {
    try { return await probeQueueHealth(); }
    catch { await new Promise(r => setTimeout(r, 300)); }
  }
  return null;
}


let running = false, runningSince = 0, timer;
let relayRunning = false;
async function relayTick() {
  if (!REPLY_RELAY_URL || !REPLY_RELAY_KEY || relayRunning) return;
  relayRunning = true;
  try { await flushReplyEvents(chrome.storage.local, REPLY_RELAY_URL, REPLY_RELAY_KEY); }
  catch (e) { await chrome.storage.local.set({replyRelayLastResult: {
    at: Date.now(), status: 'pending', error: e.message || String(e),
  }}); }
  finally { relayRunning = false; }
}
async function captureForRelay(mailbox, scan) {
  if (!REPLY_RELAY_URL || !REPLY_RELAY_KEY) return;
  const saved = await chrome.storage.local.get(['accounts', 'settings']);
  let accounts = saved.accounts || saved.settings?.accounts || [];
  if (!accounts.length) {
    try { accounts = (await (await fetch(ORIGIN + '/api/config')).json()).settings?.accounts || []; }
    catch { accounts = []; }
  }
  const tableId = accounts.find(a => String(a.mailbox || '').toLowerCase() === mailbox.toLowerCase())?.feishu_table_id || '';
  if (!/^tbl[A-Za-z0-9]+$/.test(tableId)) return;
  await captureReplyEvents(chrome.storage.local, mailbox, scan, tableId);
  relayTick();
}
const read = () => chrome.storage.local.get(
  ['config', 'current', 'client_id', 'inboxScanPending', 'rrIndex', 'slotCooldown', 'lastReplyScanAt', 'replyScanSettings']);
const save = current => chrome.storage.local.set({current});

/* ---------------- 多标签页模式（一人管多个谷歌账号） ----------------
   一个 Chrome 里可以有多个 Gmail 标签页，各自登录不同的谷歌账号。
   每个标签页只领取"它自己登录账号"的任务（poll 带 current_mailbox），
   后端是单飞（同一时刻只有 1 个任务在途），所以天然就是顺序发送：
   A 号发完 → B 号接着发，互不抢、互不切号。
*/
const MAIL_TAB_URL = 'https://mail.google.com/*';
const tabMailboxCache = new Map();   // tabId -> {mailbox, url, at}
const COOLDOWN_MS = 60000;

async function readTabMailbox(tabId, url, fresh = false) {
  if (!url || !url.startsWith('https://mail.google.com/')) return '';
  const c = tabMailboxCache.get(tabId);
  if (!fresh && c && c.url === url && Date.now() - c.at < 60000) return c.mailbox;
  try {
    const [shot] = await chrome.scripting.executeScript({
      target: {tabId},
      func: () => {
        const node = [...document.querySelectorAll('[aria-label]')].find(el => {
          const label = el.getAttribute('aria-label') || '';
          return /Google (账号|Account)/i.test(label) && /@/.test(label);
        });
        const found = (node?.getAttribute('aria-label') || '').match(/[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@gmail\.com/i);
        return found ? found[0].toLowerCase() : '';
      },
    });
    const mailbox = (shot?.result || '').toLowerCase();
    if (mailbox) {
      tabMailboxCache.set(tabId, {mailbox, url, at: Date.now()});
      const st = await chrome.storage.local.get('slotMailboxCache');
      const map = {...(st.slotMailboxCache || {}), [tabId]: mailbox};
      await chrome.storage.local.set({slotMailboxCache: map});
    }
    return mailbox;
  } catch {
    return '';
  }
}

async function listGmailSlots() {
  const tabs = await chrome.tabs.query({url: MAIL_TAB_URL});
  const provisioning = (await chrome.storage.local.get('gmailAccountProvision')).gmailAccountProvision;
  const live = tabs.filter(t => !t.discarded && t.id !== provisioning?.tabId);
  const slots = [];
  for (const t of live) {
    const mailbox = await readTabMailbox(t.id, t.url || '');
    if (mailbox) slots.push({tabId: t.id, windowId: t.windowId, mailbox, url: t.url || '', status: t.status});
  }
  if (slots.length) return slots;
  // 全部读不到（Gmail 还没渲染完 / 注入失败）：退回上次成功读到的账号，别让整队停摆。
  const cached = (await chrome.storage.local.get('slotMailboxCache')).slotMailboxCache || {};
  for (const t of live) {
    const mb = cached[t.id];
    if (mb) slots.push({tabId: t.id, windowId: t.windowId, mailbox: mb, url: t.url || '', status: t.status});
  }
  return slots;
}

async function configuredMailboxes() {
  const saved = await chrome.storage.local.get(['accounts', 'settings']);
  let accounts = Array.isArray(saved.accounts) && saved.accounts.length
    ? saved.accounts : saved.settings?.accounts;
  if (!Array.isArray(accounts) || !accounts.length) {
    const response = await fetch(ORIGIN + '/api/config', {signal: AbortSignal.timeout(4000)});
    if (!response.ok) return [];
    accounts = (await response.json()).settings?.accounts || [];
  }
  return accounts.map(a => String(a?.mailbox || '').trim().toLowerCase())
    .filter(x => /^[^\s@]+@gmail\.com$/.test(x));
}

async function ensureConfiguredGmailTabs() {
  const mailboxes = await configuredMailboxes();
  if (!mailboxes.length) return;
  const result = await ensureGmailAccountTabs(chrome, mailboxes, readTabMailbox,
    (tabId, target) => page({tabId, job: {sender: target}}, 'switch_account'));
  if (result.status === 'slot_limit')
    await chrome.storage.local.set({lastError: {at: Date.now(), message:
      'Gmail 标签页数量已达账号数，但缺少 ' + result.mailbox + '：请关闭重复账号标签页'}});
  if (result.status === 'attention')
    await chrome.storage.local.set({lastError: {at: Date.now(), message:
      '自动切换 Gmail 账号未完成：' + result.mailbox + '（' + result.reason + '）'}});
}

function slotCooling(slotCooldown, tabId) {
  const at = Number((slotCooldown || {})[tabId] || 0);
  return at && Date.now() - at < COOLDOWN_MS;
}

async function healSession(existingClientId) {
  // Prefer dedicated heal (persists token on queue); fall back to bootstrap.
  let data = null;
  try {
    const heal = await fetch(ORIGIN + '/api/session/heal', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({}),
      signal: AbortSignal.timeout(12000),
    });
    data = await heal.json();
    if (!heal.ok) data = null;
  } catch {}
  if (!data?.token) {
    const response = await fetch(ORIGIN + '/api/browser-mail/extension-bootstrap', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({}),
      signal: AbortSignal.timeout(12000),
    });
    data = await response.json();
    if (!response.ok) throw Error(data.detail || '本机独立发信队列未启动或未解锁（armed=false / 缺模板名）');
  }
  if (!/^gmw_[A-Za-z0-9_-]{20,}$/.test(data.token || ''))
    throw Error(data.detail || '会话 token 无效');
  const stored = await chrome.storage.local.get(['client_id']);
  const client_id = existingClientId || stored.client_id || crypto.randomUUID();
  const prevBag = await chrome.storage.local.get(['config', 'replyScanSettings']);
  const prevRs = prevBag.replyScanSettings
    || (prevBag.config && prevBag.config.reply_scan)
    || {enabled: true, interval_minutes: 10};
  const config = {
    token: data.token,
    account_id: data.account_id || 'multi-active',
    sender: data.sender || data.mailbox,
    template_name: data.template_name || '',
    enabled: true,
    manualPaused: false,
    reply_scan: prevRs,
  };
  await chrome.storage.local.set({config, client_id, lastError: null, replyScanSettings: prevRs});
  return {config, client_id};
}

async function rememberDevice() {
  return healSession();
}

async function api(token, name, body, _retried) {
  const r = await fetch(ORIGIN + '/api/browser-mail/worker/' + name, {
    method: 'POST',
    headers: {'Content-Type': 'application/json', Authorization: 'Bearer ' + token},
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(name === 'inbox_scan' ? 120000 : 12000),
  });
  const data = await r.json();
  if (r.status === 401 && !_retried) {
    const healed = await healSession();
    try { await fetch(ORIGIN + '/api/browser-mail/worker/state', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', Authorization: 'Bearer ' + healed.config.token},
      body: JSON.stringify({enabled: true}),
      signal: AbortSignal.timeout(12000),
    }); } catch {}
    return api(healed.config.token, name, body, true);
  }
  if (!r.ok) throw Error(data.detail || '本机队列没有确认操作');
  return data;
}

/** 把扩展内部状态回传队列，便于在 queue.log 里看清“为什么不发”。 */
let lastDiagAt = 0;
async function reportDiag(config, note, extra) {
  if (!config?.token) return;
  if (Date.now() - lastDiagAt < 8000) return;
  lastDiagAt = Date.now();
  try {
    const st = await chrome.storage.local.get(['current', 'lastPoll', 'lastError', 'slotCooldown']);
    await api(config.token, 'diag', {
      note: String(note || '').slice(0, 200),
      current: st.current ? {
        phase: st.current.phase, stage: st.current.stage,
        mailbox: st.current.mailbox, tabId: st.current.tabId,
        age_ms: Date.now() - (st.current.started || 0),
      } : null,
      lastPoll: st.lastPoll || null,
      lastError: st.lastError ? st.lastError.message : null,
      cooldown: st.slotCooldown || null,
      extra: extra || null,
    });
  } catch {}
}

/** 读当前 Gmail 标签实际登录的账号，用于优先发本账号任务、避免无谓切号。 */
async function readCurrentGmail() {
  const cached = (await chrome.storage.local.get('currentGmail')).currentGmail;
  if (cached && Date.now() - cached.at < 15000) return cached.value || '';
  try {
    const tabs = await chrome.tabs.query({url: 'https://mail.google.com/*'});
    const tab = tabs.find(t => t.active) || tabs[0];
    if (!tab) return '';
    const [shot] = await chrome.scripting.executeScript({
      target: {tabId: tab.id},
      func: () => {
        const node = [...document.querySelectorAll('[aria-label]')].find(el => {
          const label = el.getAttribute('aria-label') || '';
          return /Google (账号|Account)/i.test(label) && /@/.test(label);
        });
        const found = (node?.getAttribute('aria-label') || '').match(/[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@gmail\.com/i);
        return found ? found[0].toLowerCase() : '';
      },
    });
    const value = shot?.result || '';
    await chrome.storage.local.set({currentGmail: {value, at: Date.now()}});
    return value;
  } catch (e) {
    return '';
  }
}

async function page(current, action) {
  // switch_account: run in all frames so AccountChooser iframe rows are clickable (影刀 DOM path)
  const values = await chrome.scripting.executeScript({
    target: {tabId: current.tabId, allFrames: action === 'switch_account'},
    func: gmailPage,
    args: [action, current.job],
  });
  const results = (values || []).map(v => v?.result).filter(Boolean);
  // 账号选择器在 iframe 里：点到行就优先采信，否则主 frame 的"没头像"会触发重载循环。
  let result = results.find(r => r.switched)
    || results.find(r => r.account_row_clicked)
    || results.find(r => r.error)
    || results.find(r => r.waiting)
    || results[0] || values[0]?.result;
  if (!result) {
    // 点击账号行后 Gmail 可能整页跳转，脚本上下文被销毁，拿不到返回值属正常。
    if (action === 'switch_account') return {waiting: true, stage: '账号切换中，等待 Gmail 重新加载'};
    throw Error('Gmail 页面没有返回状态');
  }
  if (result.error && !result.waiting && !result.switched) throw Error(result.error);
  return result;
}

async function enrichReplyBodies(slot, scan) {
  const fresh = await newReplyBodyCandidates(chrome.storage.local, slot.mailbox, scan?.replies || []);
  const candidates = fresh.filter(r =>
    r?.message_id && /^https:\/\/mail\.google\.com\//.test(r.thread_url || '')
    && !r.bounce_kind && !/mailer-daemon|postmaster|no.?reply|notification@/i.test(r.from || '')
  ).slice(0, 12);
  if (!candidates.length) return scan;
  return (await withReplyDetailTab(chrome, candidates[0].thread_url, async ({tabId, navigate}) => {
    for (let i = 0; i < candidates.length; i++) {
      const r = candidates[i];
      if (i) await navigate(r.thread_url);
      const target = {tabId, job: {
        sender: slot.mailbox, recipient: r.from, message_id: r.message_id,
      }};
      let detail = null;
      for (let attempt = 0; attempt < 8; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 750));
        try { detail = await page(target, 'read_reply'); }
        catch { detail = null; }
        if (detail?.verified) break;
        if (detail?.error && !detail?.waiting) break;
      }
      if (!detail?.verified) continue;
      await markReplyBodyRead(chrome.storage.local, slot.mailbox, r.message_id);
      for (const list of [scan.replies, scan.inbox_rows, scan.spam_rows]) {
        for (const item of (list || [])) {
          if (item.message_id === r.message_id && item.from === r.from) {
            item.body = detail.body;
            item.body_verified = true;
          }
        }
      }
      if (r.unread) {
        try { await page(target, 'restore_unread'); }
        catch (e) { scan.diagnostics = {...(scan.diagnostics || {}), unread_restore_error: e.message}; }
      }
    }
    return scan;
  })) || scan;
}

/** Tabs only: 只用与任务绑定的那个标签页；不切号、不新建窗口。 */
async function ownTab(current, search = false) {
  let tab = null;
  if (current.tabId) {
    try { tab = await chrome.tabs.get(current.tabId); } catch {}
  }
  if (!tab)
    throw Error('gmail_tab_closed: 绑定的 Gmail 标签页已关闭，请重新打开该邮箱标签页并登录');
  if (/^https:\/\/accounts\.google\.com\//.test(tab.url || ''))
    throw Error('gmail_login_required: 标签页停在 Google 登录页，请手动登录 ' + (current.job.sender || ''));
  if (!tab.url?.startsWith('https://mail.google.com/')) throw Error('gmail_login_required');
  if (tab.status !== 'complete') return false;
  if (search && !current.searchStarted) {
    const url = new URL(tab.url);
    url.hash = 'search/' + encodeURIComponent('in:sent "' + current.job.marker + '"');
    await chrome.tabs.update(tab.id, {url: url.href, active: false});
    current.searchStarted = true;
    await save(current);
    return false;
  }
  return true;
}

/** 后台标签页会被 Chrome 降频，Gmail 可能不推进：先刷新，再不行就切到前台。 */
const STALL_RELOAD_MS = 25000;
const STALL_FOREGROUND_MS = 50000;

async function watchStall(current) {
  const key = (current.phase || '') + '|' + (current.stage || '');
  if (current.watchKey !== key) {
    current.watchKey = key;
    current.watchSince = Date.now();
    await save(current);
    return;
  }
  const stuck = Date.now() - (current.watchSince || Date.now());
  // 草稿已生成 / 已点发送：绝不刷新（会丢草稿或重复发送），只能切前台。
  const noReload = ['prepared', 'permit_requested', 'click_authorized', 'clicked', 'searching', 'uncertain']
    .includes(current.phase);
  if (!noReload && stuck > STALL_RELOAD_MS && (current.stallReloads || 0) < 2) {
    current.stallReloads = (current.stallReloads || 0) + 1;
    current.stage = '后台无进展，刷新 Gmail 标签页（第 ' + current.stallReloads + ' 次）';
    current.accountReady = false;
    current.watchKey = '';
    await save(current);
    try { await chrome.tabs.reload(current.tabId); } catch {}
    return;
  }
  if (stuck > STALL_FOREGROUND_MS && !current.foregrounded) {
    current.foregrounded = true;
    current.stage = '后台执行无进展，切到前台继续';
    await save(current);
    try {
      const [act] = await chrome.tabs.query({active: true, lastFocusedWindow: true});
      if (act && act.id && act.id !== current.tabId) current.restoreTabId = act.id;
      await chrome.tabs.update(current.tabId, {active: true});
      const tab = await chrome.tabs.get(current.tabId);
      if (tab.windowId) await chrome.windows.update(tab.windowId, {focused: true});
      await save(current);
    } catch {}
  }
}

async function applySwitchedTo(config, switchedTo, extraMessage, current) {
  // 多标签页模式：每个标签页固定自己的账号，不再“停留在切换后的号”，也不再改 config.sender。
  const patch = {
    lastResult: {at: Date.now(), message: extraMessage || '任务完成'},
    current: null,
  };
  // 之前为了跑通把标签页切到了前台，收尾时把用户原来的标签页切回来。
  const restore = current?.restoreTabId;
  if (restore) {
    try {
      const t = await chrome.tabs.get(restore);
      await chrome.tabs.update(t.id, {active: true});
      if (t.windowId) await chrome.windows.update(t.windowId, {focused: true});
    } catch {}
  }
  await chrome.storage.local.set(patch);
}

async function finish(config, current) {
  let fin;
  try {
    fin = await api(config.token, 'finish', {
      job_id: current.job.job_id,
      lease: current.job.lease,
      receipt: current.receipt,
    });
  } catch (e) {
    // 队列侧报错时不能每 2.5 秒重报一次：会刷爆回执并让 poll 饿死。
    const attempts = (current.finishAttempts || 0) + 1;
    if (attempts < 3) {
      current.finishAttempts = attempts;
      await save(current);
      await chrome.storage.local.set({
        lastError: {at: Date.now(), message: '回执上报失败(' + attempts + '/3)：' + e.message + '，稍后重试'},
      });
      return;
    }
    await chrome.storage.local.set({
      current: null,
      lastError: {at: Date.now(), message: '回执连续失败，已停止重试；任务仍留在队列，请查看 8767 日志：' + e.message},
      lastResult: {at: Date.now(), message: '回执上报失败，已停止重试（不重复发送）'},
    });
    return;
  }
  // Jim order: send → inbox scan (same mailbox) → THEN switch. Never switch before scan.
  let switchedTo = '';
  let scanNote = '';
  if (fin && fin.need_inbox_scan && current.receipt?.status === 'sent') {
    const deferScan = async (message) => chrome.storage.local.set({
      inboxScanPending: {
        at: Date.now(), mailbox: current.job.sender, recipient: current.job.recipient,
        marker: current.job.marker, job_id: current.job.job_id,
        token: config.token, tabId: current.tabId || null,
      },
      current: null,
      lastResult: {at: Date.now(), message},
    });
    try {
      // Ensure we are still on the sending mailbox tab before scanning.
      if (!await ownTab(current)) {
        // Tab not ready; keep a soft pending so next tick can retry scan via storage flag.
        await deferScan('已发送；等待 Gmail 标签页就绪后扫描收件箱');
        return;
      }
      let scan = await page(current, 'scan_inbox');
      if (!scan?.scanned) {
        await deferScan('已发送；Gmail 收件箱尚未就绪，稍后补扫');
        return;
      }
      scan = await enrichReplyBodies({mailbox: current.job.sender}, scan);
      await captureForRelay(current.job.sender, scan);
      const scanBody = {
        mailbox: current.job.sender,
        recipient: current.job.recipient,
        marker: current.job.marker,
        job_id: current.job.job_id,
        replies_found: !!(scan && (scan.replies_found || (scan.replies || []).length)),
        replies: (scan && scan.replies) || [],
        inbox_rows: (scan && scan.inbox_rows) || [],
        spam_rows: (scan && scan.spam_rows) || [],
        stage: (scan && scan.stage) || '',
        diagnostics: (scan && scan.diagnostics) || {},
        no_switch: true,   // 多标签页模式：发完只扫本号收件箱，不切号
      };
      const scanned = await api(config.token, 'inbox_scan', {scan: scanBody});
      const rw = (scanned && scanned.reply_writeback) || [];
      const rwOk = rw.filter(x => x && x.ok && !x.skipped).length;
      const susN = rw.filter(x => x && x.suspicious).length;
      const spamN = Number(scanned?.spam_rows || 0);
      scanNote = scanned?.replies_found
        ? (' · 收件箱扫描到回复')
        : (' · 收件箱已扫描无新回复');
      if (spamN) scanNote += ' · 垃圾/可疑邮件 ' + spamN + ' 条已一并扫描';
      if (susN) scanNote += ' · 其中标记可疑 ' + susN + ' 条（不影响后续发送，换模板即可）';
      const bw = (scanned && scanned.bounce_writeback) || [];
      const hardN = bw.filter(x => x && x.ok && x.label === '退信').length;
      const blockN = bw.filter(x => x && x.ok && x.label === '可疑拦截').length;
      if (hardN) scanNote += ' · 退信 ' + hardN + ' 条';
      if (blockN) scanNote += ' · 可疑拦截 ' + blockN + ' 条（可换模板重试）';
      if (rwOk) scanNote += ' · 已回写回复时间 ' + rwOk + ' 条';
      if (fin.writeback && fin.writeback.ok === false)
        scanNote += ' · 飞书写回失败:' + (fin.writeback.reason || '');
      else if (fin.writeback && fin.writeback.ok)
        scanNote += ' · 飞书已写回发送状态';
    } catch (e) {
      await deferScan('收件箱扫描失败，稍后补扫：' + e.message);
      return;
    }
  }
  const msg = current.receipt.status === 'sent'
    ? ('已核对 Gmail 已发邮件：' + current.job.recipient + scanNote)
    : current.receipt.error;
  await applySwitchedTo(config, '', msg, current);
  // Keep Gmail tab open (never close the whole Chrome window).
}


async function replyScanSettingsFrom(stored) {
  const cfg = stored?.config || {};
  const rs = (cfg.reply_scan && typeof cfg.reply_scan === 'object')
    ? cfg.reply_scan
    : (stored?.replyScanSettings && typeof stored.replyScanSettings === 'object'
      ? stored.replyScanSettings
      : {enabled: true, interval_minutes: 10});
  return {
    enabled: rs.enabled !== false,
    interval_minutes: Math.max(3, Math.min(180, Number(rs.interval_minutes) || 10)),
  };
}

async function maybePeriodicReplyScan(config, stored) {
  // 与发信解耦：暂停发送 / Y=0 / 0 封 时仍可扫；仅「定期查回信」关才停。
  try {
    const rs = await replyScanSettingsFrom(stored);
    if (!rs.enabled) return;
    if (!config?.token) return;
    if (stored?.current) return;
    if (stored?.inboxScanPending) return;
    const last = Number(stored?.lastReplyScanAt || 0);
    const gapMs = rs.interval_minutes * 60 * 1000;
    if (last && Date.now() - last < gapMs) return;
    const slots = await listGmailSlots();
    if (!slots.length) {
      await reportDiag(config, 'reply_scan_no_gmail_slot');
      return;
    }
    const notes = [];
    let incomplete = false;
    for (const slot of slots) {
      const fake = {
        tabId: slot.tabId,
        job: {sender: slot.mailbox, recipient: '', marker: '', job_id: ''},
      };
      try {
        if (!(await ownTab(fake))) {
          notes.push(slot.mailbox + '=tab_unusable');
          incomplete = true;
          continue;
        }
        let scan = await page(fake, 'scan_inbox');
        if (scan?.waiting) {
          await new Promise(r => setTimeout(r, 2800));
          scan = await page(fake, 'scan_inbox');
        }
        if (!scan?.scanned) {
          notes.push(slot.mailbox + '=' + (scan?.error || scan?.stage || 'not_ready'));
          incomplete = true;
          continue;
        }
        scan = await enrichReplyBodies(slot, scan);
        await captureForRelay(slot.mailbox, scan);
        const scanned = await api(config.token, 'inbox_scan', {
          scan: {
            mailbox: slot.mailbox,
            recipient: '',
            marker: '',
            job_id: '',
            reason: 'periodic_reply',
            replies_found: !!(scan && (scan.replies_found || (scan.replies || []).length)),
            replies: (scan && scan.replies) || [],
            inbox_rows: (scan && scan.inbox_rows) || [],
            spam_rows: (scan && scan.spam_rows) || [],
            no_switch: true,
          },
        });
        const rw = (scanned && scanned.reply_writeback) || [];
        const rwOk = rw.filter(x => x && x.ok && !x.skipped).length;
        const bw = (scanned && scanned.bounce_writeback) || [];
        notes.push(
          slot.mailbox.split('@')[0]
          + ':回复' + ((scanned && scanned.replies_found) ? ((scanned.replies || []).length) : 0)
          + '/回写' + rwOk
          + '/退信类' + bw.length
        );
      } catch (e) {
        notes.push(slot.mailbox + '=err:' + (e.message || e));
        incomplete = true;
      }
    }
    // A loading mailbox is not a completed scan. Retry after one minute,
    // while fully completed rounds honor the configured interval.
    await chrome.storage.local.set({lastReplyScanAt: incomplete ? Date.now() - gapMs + 60000 : Date.now()});
    await reportDiag(config, 'periodic_reply_scan', {notes: notes.slice(0, 8), interval_minutes: rs.interval_minutes});
    await chrome.storage.local.set({
      lastResult: {at: Date.now(), message: '定期查回信完成 · ' + notes.join(' · ')},
    });
  } catch (e) {
    try {
      await chrome.storage.local.set({
        lastError: {at: Date.now(), message: '定期查回信：' + (e.message || e)},
      });
    } catch {}
  }
}

async function tick() {
  // 看门狗：某次 tick 卡死（await 永不返回）时不能把整个队列冻住。
  if (running && Date.now() - runningSince < 180000) return;
  running = true;
  runningSince = Date.now();
  let current, config;
  try {
    let stored = await read();
    config = stored.config;
    current = stored.current;
    if (current?.receipt) { await finish(config, current); return; }
    // Resume deferred inbox scan (send→scan→switch) if tab was not ready at finish time.
    if (!current && stored.inboxScanPending && config?.token) {
      const pendingScan = stored.inboxScanPending;
      const fake = {
        tabId: pendingScan.tabId,
        job: {
          sender: pendingScan.mailbox,
          recipient: pendingScan.recipient,
          marker: pendingScan.marker,
          job_id: pendingScan.job_id,
        },
      };
      try {
        if (await ownTab(fake)) {
          let scan = await page(fake, 'scan_inbox');
          if (!scan?.scanned) return;
          scan = await enrichReplyBodies({mailbox: pendingScan.mailbox}, scan);
          await captureForRelay(pendingScan.mailbox, scan);
          const scanned = await api(config.token, 'inbox_scan', {
            scan: {
              mailbox: pendingScan.mailbox,
              recipient: pendingScan.recipient,
              marker: pendingScan.marker,
              job_id: pendingScan.job_id,
              replies_found: !!(scan && (scan.replies_found || (scan.replies || []).length)),
              replies: (scan && scan.replies) || [],
              inbox_rows: (scan && scan.inbox_rows) || [],
              spam_rows: (scan && scan.spam_rows) || [],
              no_switch: true,
            },
          });
          const rw = (scanned && scanned.reply_writeback) || [];
          const rwOk = rw.filter(x => x && x.ok && !x.skipped).length;
          await chrome.storage.local.set({inboxScanPending: null});
          await applySwitchedTo(config, '', '补做收件箱扫描完成' + (rwOk ? (' · 回写回复时间 ' + rwOk + ' 条') : ''), fake);
        }
      } catch (e) {
        await chrome.storage.local.set({
          lastError: {at: Date.now(), message: 'inbox scan resume: ' + e.message},
        });
      }
    }
    if (!current && !config?.manualPaused && (!config?.token || !config.enabled)) {
      stored = config?.token
        ? {...stored, config: {...config, enabled: true}}
        : await rememberDevice();
      if (!stored) return;
      config = stored.config;
      if (config?.token && stored.client_id) {
        await api(config.token, 'state', {enabled: true});
        await chrome.storage.local.set({config});
      }
    }
    // 定期查回信：与「扩展发送 enabled」解耦；暂停发信仍可扫。
    if (!current && config?.token) {
      try { await ensureConfiguredGmailTabs(); }
      catch (e) { await reportDiag(config, 'gmail_tab_setup: ' + (e.message || e)); }
      await maybePeriodicReplyScan(config, stored);
      stored = await read();
      config = stored.config || config;
      current = stored.current;
    }
    if (!config?.enabled || !config.token) return;
    if (!current) {
      const slots = await listGmailSlots();
      if (!slots.length) {
        await chrome.storage.local.set({
          lastError: {at: Date.now(), message: '等待插件打开并登录 Gmail 账号标签页'},
        });
        await reportDiag(config, 'no_gmail_slot');
        return;
      }
      // 轮转起点：拿到过任务的号下一次排到后面，避免一个号长期霸占队列。
      const rr = Number(stored.rrIndex || 0) % slots.length;
      const order = slots.slice(rr).concat(slots.slice(0, rr));
      const cooldown = stored.slotCooldown || (await chrome.storage.local.get('slotCooldown')).slotCooldown;
      let picked = null, reason = '';
      for (const slot of order) {
        if (slotCooling(cooldown, slot.tabId)) { reason = 'cooldown'; continue; }
        const r = await api(config.token, 'poll', {
          client_id: stored.client_id,
          current_mailbox: slot.mailbox,
        });
        if (r.job) { picked = {slot, job: r.job}; break; }
        reason = slot.mailbox + ':' + (r.reason || 'no_job');
      }
      if (!picked) {
        await chrome.storage.local.set({
          lastPoll: {at: Date.now(), reason: reason.slice(0, 140), slots: slots.map(s => s.mailbox)},
        });
        await reportDiag(config, 'no_job', {reason: reason.slice(0, 140)});
        return;
      }
      current = {
        job: picked.job,
        phase: 'preparing',
        started: Date.now(),
        tabId: picked.slot.tabId,
        mailbox: picked.slot.mailbox,
        noSwitch: true,
        stage: '已绑定标签页 ' + picked.slot.mailbox,
      };
      await save(current);
      await chrome.storage.local.set({rrIndex: rr + 1});
      stored.rrIndex = rr + 1;
    }
    // 不切号了，只剩“准备+点击+核对”一段预算，给足 200 秒。
    const sendBudgetMs = 200000;
    const phaseStarted = current.accountReady ? (current.sendStarted || current.started) : current.started;
    if (Date.now() - phaseStarted > sendBudgetMs)
      throw Error('网页处理超时：' + (current.stage || '阶段未知') + '；保留草稿或已发记录等待核查');
    if (!await ownTab(current)) return;
    await watchStall(current);
    if (current.phase === 'preparing') {
      // 多标签页模式：这个标签页本来就登录着 job.sender，不再做“头像→切号”。
      // 万一账号不符，page('prepare') 会返回 wrong_gmail_account，绝不会发错号。
      if (!current.accountReady) {
        current.accountReady = true;
        current.sendStarted = Date.now();
        current.stage = '使用已登录标签页 ' + (current.job.sender || '');
        await save(current);
        try {
          await api(config.token, 'progress', {
            job_id: current.job.job_id,
            stage: current.stage,
            account_ready: true,
            clear_gmail_ui_switch: true,
          });
        } catch {}
      }
      const result = await page(current, 'prepare');
      if (result.stage && result.stage !== current.stage) {
        current.stage = result.stage;
        await save(current);
        try {
          await api(config.token, 'progress', {
            job_id: current.job.job_id,
            stage: result.stage,
            diagnostics: result.diagnostics || {},
          });
        } catch {}
      }
      if (!result.prepared) return;
      current.phase = 'prepared';
      await save(current);
    }
    if (canClick(current.phase)) {
      const snapshot = await page(current, 'snapshot');
      if (!snapshot.snapshot) throw Error('Gmail 草稿快照不可用');
      current.phase = 'permit_requested';
      await save(current);
      await api(config.token, 'permit', {
        job_id: current.job.job_id,
        lease: current.job.lease,
        sender: current.job.sender,
        prepared_snapshot: snapshot.snapshot,
      });
      current.phase = 'click_authorized';
      await save(current);
      await page(current, 'send');
      current.phase = 'clicked';
      await save(current);
      return;
    }
    if (current.phase === 'clicked') {
      const result = await page(current, 'submitted');
      if (!result.submitted && Date.now() - (current.sendStarted || current.started) < 20000) return;
      current.phase = 'searching';
      await save(current);
    }
    if (needsReconciliation(current.phase)) {
      current.phase = 'searching';
      await save(current);
      if (!await ownTab(current, true)) return;
      const receipt = await page(current, 'receipt');
      if (!receiptMatches(current.job, receipt)) return;
      current.receipt = receipt;
      await save(current);
      await finish(config, current);
    }
  } catch (error) {
    await chrome.storage.local.set({lastError: {at: Date.now(), message: error.message}});
    await reportDiag(config, 'tick_error: ' + error.message);
    // 标签页登录的不是这个号（或已跳到登录页）：冷却该标签页 60 秒，别原地打转。
    if (current?.tabId && /wrong_gmail_account|gmail_login_required|gmail_tab_closed/i.test(error.message)) {
      const cd = (await chrome.storage.local.get('slotCooldown')).slotCooldown || {};
      cd[current.tabId] = Date.now();
      await chrome.storage.local.set({slotCooldown: cd});
    }
    if (current && !current.receipt) {
      current.receipt = {
        status: needsReconciliation(current.phase) ? 'uncertain' : 'paused',
        error: error.message,
      };
      await save(current);
      try { await finish(config, current); } catch {}
      // 只有 Gmail 真报“发信超限”才暂停；账号不符/超时/可疑邮件都不再自动禁发（换模板即可）。
      if (/gmail_limit|reached.*limit.*sending|已达到发送.*上限/i.test(error.message))
        await chrome.storage.local.set({config: {...config, enabled: false, manualPaused: true}});
    }
  } finally {
    running = false;
    clearTimeout(timer);
    const state = await read();
    const rsOn = ((state.config?.reply_scan?.enabled) !== false)
      && ((state.replyScanSettings?.enabled) !== false);
    if (state.config?.enabled || state.current?.receipt || rsOn) timer = setTimeout(tick, 2500);
  }
}

chrome.alarms.onAlarm.addListener(a => { if (a.name === 'send-plugin-heartbeat') { tick(); relayTick(); } });
chrome.runtime.onInstalled.addListener(() => chrome.alarms.create('send-plugin-heartbeat', {periodInMinutes: 0.5}));
chrome.runtime.onStartup.addListener(async () => {
  await chrome.alarms.create('send-plugin-heartbeat', {periodInMinutes: 0.5});
});
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id !== chrome.runtime.id) return;
  if (message.type === 'wake') { tick(); reply({ok: true}); return true; }
  if (message.type === 'reply-scan-settings') {
    const rs = message.reply_scan || {enabled: true, interval_minutes: 10};
    chrome.storage.local.get('config').then(async s => {
      const cfg = {...(s.config || {}), reply_scan: rs};
      await chrome.storage.local.set({config: cfg, replyScanSettings: rs});
      tick();
      reply({ok: true});
    }).catch(e => reply({ok: false, error: e.message}));
    return true;
  }
  if (message.type === 'heal') {
    healSession().then(async h => {
      try { await api(h.config.token, 'state', {enabled: true}); } catch {}
      tick();
      reply({ok: true, sender: h.config.sender});
    }).catch(e => reply({ok: false, error: e.message}));
    return true;
  }
});
chrome.alarms.create('send-plugin-heartbeat', {periodInMinutes: 0.5});
cleanupReplyDetailTab(chrome).catch(() => {}).finally(() => { tick(); relayTick(); });
