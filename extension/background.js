import {gmailPage} from './page.js';
import {ORIGIN, accountURL, canClick, needsReconciliation, receiptMatches} from './protocol.js';

const NATIVE_HOST = 'com.tk.outreach.sendqueue';
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
    const t = setTimeout(() => finish({ok: false, error: 'timeout'}), 15000);
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
const read = () => chrome.storage.local.get(
  ['config', 'current', 'client_id', 'inboxScanPending', 'rrIndex', 'slotCooldown']);
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

async function readTabMailbox(tabId, url) {
  if (!url || !url.startsWith('https://mail.google.com/')) return '';
  const c = tabMailboxCache.get(tabId);
  if (c && c.url === url && Date.now() - c.at < 60000) return c.mailbox;
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
  const live = tabs.filter(t => !t.discarded);
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
  const config = {
    token: data.token,
    account_id: data.account_id || 'multi-active',
    sender: data.sender || data.mailbox,
    template_name: data.template_name || '',
    enabled: true,
    manualPaused: false,
  };
  await chrome.storage.local.set({config, client_id, lastError: null});
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
    signal: AbortSignal.timeout(12000),
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
    try {
      // Ensure we are still on the sending mailbox tab before scanning.
      if (!await ownTab(current)) {
        // Tab not ready; keep a soft pending so next tick can retry scan via storage flag.
        await chrome.storage.local.set({
          inboxScanPending: {
            at: Date.now(),
            mailbox: current.job.sender,
            recipient: current.job.recipient,
            marker: current.job.marker,
            job_id: current.job.job_id,
            token: config.token,
            tabId: current.tabId || null,
          },
          current: null,
          lastResult: {
            at: Date.now(),
            message: '已发送；等待 Gmail 标签页就绪后扫描收件箱（切号前）',
          },
        });
        return;
      }
      const scan = await page(current, 'scan_inbox');
      const scanBody = {
        mailbox: current.job.sender,
        recipient: current.job.recipient,
        marker: current.job.marker,
        job_id: current.job.job_id,
        replies_found: !!(scan && (scan.replies_found || (scan.replies || []).length)),
        replies: (scan && scan.replies) || [],
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
      try {
        await api(config.token, 'inbox_scan', {
          scan: {
            mailbox: current.job.sender,
            recipient: current.job.recipient,
            marker: current.job.marker,
            job_id: current.job.job_id,
            replies_found: false,
            replies: [],
            error: e.message,
            no_switch: true,
          },
        });
      } catch {}
      scanNote = ' · 收件箱扫描失败:' + e.message;
    }
  }
  const msg = current.receipt.status === 'sent'
    ? ('已核对 Gmail 已发邮件：' + current.job.recipient + scanNote)
    : current.receipt.error;
  await applySwitchedTo(config, '', msg, current);
  // Keep Gmail tab open (never close the whole Chrome window).
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
          const scan = await page(fake, 'scan_inbox');
          const scanned = await api(config.token, 'inbox_scan', {
            scan: {
              mailbox: pendingScan.mailbox,
              recipient: pendingScan.recipient,
              marker: pendingScan.marker,
              job_id: pendingScan.job_id,
              replies_found: !!(scan && (scan.replies_found || (scan.replies || []).length)),
              replies: (scan && scan.replies) || [],
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
    if (!config?.enabled || !config.token) return;
    if (!current) {
      const slots = await listGmailSlots();
      if (!slots.length) {
        await chrome.storage.local.set({
          lastError: {at: Date.now(), message: '没有可用 Gmail 标签页：每个谷歌账号各开一个 mail.google.com 标签页并登录'},
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
    if (state.config?.enabled || state.current?.receipt) timer = setTimeout(tick, 2500);
  }
}

chrome.alarms.onAlarm.addListener(a => { if (a.name === 'send-plugin-heartbeat') tick(); });
chrome.runtime.onInstalled.addListener(() => chrome.alarms.create('send-plugin-heartbeat', {periodInMinutes: 0.5}));
chrome.runtime.onStartup.addListener(async () => {
  await chrome.alarms.create('send-plugin-heartbeat', {periodInMinutes: 0.5});
  tick();
});
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id !== chrome.runtime.id) return;
  if (message.type === 'wake') { tick(); reply({ok: true}); return true; }
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
tick();


/* ===== Auto-update (public GitHub tk-send-plugin) ===== */
const UPDATE_REPO = '987144016/tk-send-plugin';
const UPDATE_VERSION_URL = `https://raw.githubusercontent.com/${UPDATE_REPO}/main/VERSION`;
const UPDATE_ALARM = 'send-plugin-update-check';
const UPDATE_PERIOD_MIN = 360; // 6h

function parseVerTuple(s) {
  s = String(s || '').trim().replace(/^[vV]/, '');
  const [core, ...rest] = s.split('-');
  const parts = core.split('.').map(x => {
    const n = parseInt(x, 10);
    return Number.isFinite(n) ? n : 0;
  });
  while (parts.length < 3) parts.push(0);
  return { nums: parts.slice(0, 4), suffix: rest.join('-') || '' };
}

function isRemoteNewer(remote, local) {
  if (!remote) return false;
  if (!local) return true;
  const r = parseVerTuple(remote);
  const l = parseVerTuple(local);
  for (let i = 0; i < Math.max(r.nums.length, l.nums.length); i++) {
    const a = r.nums[i] || 0, b = l.nums[i] || 0;
    if (a !== b) return a > b;
  }
  return false; // same core → not newer
}

function nativeCmd(cmd, timeoutMs = 60000) {
  return new Promise((resolve) => {
    let port;
    try { port = chrome.runtime.connectNative(NATIVE_HOST); }
    catch (e) { resolve({ok: false, error: String(e)}); return; }
    let done = false;
    const finish = (o) => { if (done) return; done = true; try { port.disconnect(); } catch {} resolve(o); };
    const t = setTimeout(() => finish({ok: false, error: 'timeout'}), timeoutMs);
    port.onMessage.addListener((msg) => { clearTimeout(t); finish(msg || {ok: true}); });
    port.onDisconnect.addListener(() => {
      clearTimeout(t);
      finish({ok: false, error: (chrome.runtime.lastError && chrome.runtime.lastError.message) || 'disconnect'});
    });
    try { port.postMessage({cmd}); }
    catch (e) { clearTimeout(t); finish({ok: false, error: String(e)}); }
  });
}

async function fetchRemoteVersion() {
  try {
    const r = await fetch(UPDATE_VERSION_URL, {cache: 'no-store', signal: AbortSignal.timeout(12000)});
    if (!r.ok) throw Error('HTTP ' + r.status);
    const text = (await r.text()).trim();
    if (text) return {ok: true, remote: text, source: 'branch_VERSION'};
  } catch (e) {
    /* fall through */
  }
  try {
    const r = await fetch(`https://api.github.com/repos/${UPDATE_REPO}/releases/latest`, {
      headers: {Accept: 'application/vnd.github+json'},
      signal: AbortSignal.timeout(12000),
    });
    if (!r.ok) throw Error('HTTP ' + r.status);
    const j = await r.json();
    const tag = (j.tag_name || '').trim();
    if (tag) return {ok: true, remote: tag.replace(/^[vV]/, ''), source: 'release_tag', tag};
  } catch (e) {
    return {ok: false, error: String(e && e.message || e)};
  }
  return {ok: false, error: 'no remote version'};
}

async function localVersion() {
  const n = await nativeCmd('version', 8000);
  if (n && n.ok && n.local) return n.local;
  try { return chrome.runtime.getManifest().version; } catch { return ''; }
}

async function setUpdateBadge(info) {
  try {
    if (info && info.needsReload) {
      await chrome.action.setBadgeText({text: '↑'});
      await chrome.action.setBadgeBackgroundColor({color: '#b45309'});
      await chrome.action.setTitle({title: `TK Send Plugin: 已更新到 ${info.after || info.remote || ''}，请在 chrome://extensions 重新加载（或点徽章后看 Options）`});
    } else if (info && info.available) {
      await chrome.action.setBadgeText({text: '↑'});
      await chrome.action.setBadgeBackgroundColor({color: '#176d52'});
      await chrome.action.setTitle({title: `TK Send Plugin: 有新版本 ${info.remote}（当前 ${info.local}）`});
    } else {
      await chrome.action.setBadgeText({text: ''});
      await chrome.action.setTitle({title: 'TK Send Plugin (Gmail Web)'});
    }
  } catch {}
}

async function checkAndApplyUpdate({force = false} = {}) {
  const rem = await fetchRemoteVersion();
  if (!rem.ok) {
    await chrome.storage.local.set({updateState: {at: Date.now(), ok: false, error: rem.error}});
    return rem;
  }
  const local = await localVersion();
  const newer = isRemoteNewer(rem.remote, local);
  if (!newer && !force) {
    const st = {at: Date.now(), ok: true, available: false, local, remote: rem.remote, source: rem.source};
    await chrome.storage.local.set({updateState: st});
    await setUpdateBadge(null);
    return st;
  }
  // Prefer Native Messaging apply (git pull or zip overlay)
  const applied = await nativeCmd('apply_update', 120000);
  if (applied && applied.ok && applied.updated) {
    const st = {
      at: Date.now(), ok: true, available: false, updated: true, needsReload: true,
      local, remote: rem.remote, after: applied.after, method: applied.method,
    };
    await chrome.storage.local.set({updateState: st});
    await setUpdateBadge(st);
    // Auto-reload when safe (no in-flight send job)
    try {
      const state = await read();
      const busy = !!(state.current && (state.current.job || state.current.receipt || state.current.stage));
      if (!busy) {
        chrome.runtime.reload();
        return st;
      }
    } catch {}
    return st;
  }
  // Could not apply automatically — surface availability + hint manual script
  const st = {
    at: Date.now(), ok: true, available: newer, updated: false,
    local, remote: rem.remote, source: rem.source,
    applyError: applied && (applied.error || applied.reason),
    hint: '自动更新失败时请双击 Update-SendPlugin.ps1，然后在 chrome://extensions 重新加载扩展',
  };
  await chrome.storage.local.set({updateState: st});
  await setUpdateBadge(st);
  return st;
}

async function ensureUpdateAlarm() {
  await chrome.alarms.create(UPDATE_ALARM, {periodInMinutes: UPDATE_PERIOD_MIN});
}

chrome.alarms.onAlarm.addListener(a => {
  if (a.name === UPDATE_ALARM) checkAndApplyUpdate().catch(() => {});
});
chrome.runtime.onInstalled.addListener(() => { ensureUpdateAlarm(); checkAndApplyUpdate().catch(() => {}); });
chrome.runtime.onStartup.addListener(() => { ensureUpdateAlarm(); checkAndApplyUpdate().catch(() => {}); });
ensureUpdateAlarm();
// First check shortly after SW starts (avoid racing send tick)
setTimeout(() => checkAndApplyUpdate().catch(() => {}), 8000);

// message hooks
const _prevOnMessage = chrome.runtime.onMessage.hasListeners ? null : null;
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id !== chrome.runtime.id) return;
  if (message.type === 'check_update') {
    checkAndApplyUpdate({force: !!message.force}).then(r => reply(r)).catch(e => reply({ok: false, error: String(e)}));
    return true;
  }
  if (message.type === 'get_update_state') {
    chrome.storage.local.get('updateState').then(s => reply(s.updateState || null));
    return true;
  }
});

