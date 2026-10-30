import {ORIGIN} from './protocol.js';
import {REPLY_RELAY_URL, REPLY_RELAY_KEY} from './relay-config.js';
import {chooseInitialSettings} from './options-state.mjs';

const el = id => document.getElementById(id);

let toastTimer = null;
let toastStickyUntil = 0;
function showToast(message, kind = 'info', stickyMs = 10000) {
  const t = el('toast');
  if (!t) return;
  t.className = 'show ' + (kind || 'info');
  t.textContent = message;
  toastStickyUntil = Date.now() + stickyMs;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    if (Date.now() >= toastStickyUntil) {
      t.className = '';
      t.textContent = '';
    }
  }, stickyMs + 50);
}

async function healExtensionSession() {
  // Re-issue/persist queue token + push into extension storage via background heal.
  let token = null, mailbox = null, pending = null;
  try {
    const r = await fetch(ORIGIN + '/api/session/heal', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({}),
      signal: AbortSignal.timeout(12000),
    });
    const data = await r.json();
    if (!r.ok) throw Error(data.detail || 'heal 失败');
    token = data.token;
    mailbox = data.mailbox;
    pending = data.pending;
  } catch (e) {
    // fall back to bootstrap
    const r = await fetch(ORIGIN + '/api/browser-mail/extension-bootstrap', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({}),
      signal: AbortSignal.timeout(12000),
    });
    const data = await r.json();
    if (!r.ok) throw Error(data.detail || 'bootstrap 失败');
    token = data.token;
    mailbox = data.sender;
  }
  if (!token) throw Error('heal 未返回 token');
  const old = await chrome.storage.local.get(['client_id', 'config']);
  const client_id = old.client_id || crypto.randomUUID();
  const config = {
    token,
    account_id: 'multi-active',
    sender: mailbox || old.config?.sender || activeMailbox,
    enabled: true,
    manualPaused: false,
  };
  await chrome.storage.local.set({config, client_id, lastError: null});
  try { await api(token, 'state', {enabled: true}); } catch {}
  try { await chrome.runtime.sendMessage({type: 'heal'}); } catch {
    try { await chrome.runtime.sendMessage({type: 'wake'}); } catch {}
  }
  return {token, mailbox, pending};
}


const SEED = [
  {
    mailbox: '',
    template_name: '',
    feishu_table_name: '',
    feishu_table_id: '',
    window_minutes: 30,
    batch_size: 15,
  },
];

let accounts = SEED.map(a => ({...a}));
let activeMailbox = SEED[0].mailbox;
let draftTimer = null;
let draftRevision = 0;

function normalizeMailbox(v) {
  return (v || '').trim().toLowerCase();
}

function readAccountsFromDom() {
  const nodes = [...document.querySelectorAll('.acct')];
  return nodes.map((node, idx) => {
    const tableName = (node.querySelector('.f-table-name').value || '').trim();
    const previous = accounts[idx] || {};
    return {
      mailbox: normalizeMailbox(node.querySelector('.f-mailbox').value),
      template_name: (node.querySelector('.f-template').value || '').trim(),
      feishu_table_name: tableName,
      feishu_table_id: tableName === previous.feishu_table_name ? (previous.feishu_table_id || '') : '',
      window_minutes: Math.max(1, parseInt(node.querySelector('.f-x').value, 10) || 30),
      batch_size: (() => { const n = parseInt(node.querySelector('.f-y').value, 10); return Number.isFinite(n) ? Math.max(0, n) : 15; })(),
    };
  });
}

function draftFromUi() {
  const activeNode = document.querySelector('.acct.active .f-mailbox');
  return {
    accounts: readAccountsFromDom(),
    active_mailbox: normalizeMailbox(activeNode?.value) || activeMailbox,
    reply_scan: {enabled: el('reply-scan-enabled')?.checked !== false,
      interval_minutes: Number(el('reply-scan-interval')?.value) || 10},
  };
}

async function persistDraft() {
  await chrome.storage.local.set({draftSettings: draftFromUi()});
}

function scheduleDraftSave() {
  draftRevision += 1;
  clearTimeout(draftTimer);
  draftTimer = setTimeout(() => { persistDraft().catch(() => {}); }, 250);
}

function validateAccounts(list, active) {
  if (!list.length) throw Error('至少保留一个账号');
  const seen = new Set();
  for (const a of list) {
    if (!/^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@gmail\.com$/i.test(a.mailbox))
      throw Error('无效 Gmail：' + (a.mailbox || '(空)'));
    if (seen.has(a.mailbox)) throw Error('重复邮箱：' + a.mailbox);
    seen.add(a.mailbox);
    if (!a.template_name) throw Error(a.mailbox + ' 缺少 Gmail邮件模板名');
    if (!a.feishu_table_name)
      throw Error(a.mailbox + ' 请填写飞书表名');
    if (!Number.isFinite(a.batch_size) || a.batch_size < 0 || a.batch_size > 100) throw Error('一次发多少人须 0–100（0=暂时禁用本号；禁止无限制群发）');
    if (a.window_minutes < 1 || a.window_minutes > 1440) throw Error('批次间隔须 1–1440 分钟');
  }
  // cross-account template ban
  const byTpl = {};
  for (const a of list) {
    const t = a.template_name;
    if (byTpl[t] && byTpl[t] !== a.mailbox)
      throw Error('模板名禁止跨账号共用：「' + t + '」已被 ' + byTpl[t] + ' 使用');
    byTpl[t] = a.mailbox;
  }
  if (!list.some(a => a.mailbox === active))
    throw Error('active 必须在账号列表中');
}

function renderAccounts() {
  const box = el('accounts');
  box.innerHTML = '';
  accounts.forEach((a, idx) => {
    const isActive = a.mailbox === activeMailbox;
    const div = document.createElement('div');
    div.className = 'acct' + (isActive ? ' active' : '');
    div.dataset.idx = String(idx);
    div.innerHTML = `
      <div><strong>账号 ${idx + 1}</strong>${isActive ? '<span class="badge">当前发件号</span>' : ''}</div>
      <div class="grid">
        <div class="full">
          <label>Gmail 邮箱</label>
          <input class="f-mailbox" type="email" value="${escapeAttr(a.mailbox)}" placeholder="name@gmail.com">
        </div>
        <div class="full">
          <label>Gmail邮件模板名</label>
          <input class="f-template" type="text" value="${escapeAttr(a.template_name)}" placeholder="Gmail邮件模板名">
          <div class="hint">禁止填别人邮箱的模板</div>
        </div>
        <div class="full">
          <label>飞书表名</label>
          <input class="f-table-name" type="text" value="${escapeAttr(a.feishu_table_name)}" placeholder="填写自己的飞书数据表名称">
          <div class="hint">保存时自动查找对应数据表</div>
        </div>
        <div>
          <label>一次发多少人（上限）</label>
          <input class="f-y" type="number" min="0" max="100" value="${a.batch_size}">
          <div class="hint">0 = 暂时禁用本号，不入队不发；发满这个人数就停，等下个批次再发；不是一次性群发全部</div>
        </div>
        <div>
          <label>批次间隔（分钟）</label>
          <input class="f-x" type="number" min="1" max="1440" value="${a.window_minutes}">
          <div class="hint">这一批发完后，隔多久开始下一批</div>
        </div>
      </div>
      <div class="acct-actions">
        <button type="button" class="tiny secondary btn-active" ${isActive ? 'disabled' : ''}>设为当前发件号</button>
        <button type="button" class="tiny danger btn-del" ${accounts.length <= 1 ? 'disabled' : ''}>删除</button>
      </div>`;
    box.appendChild(div);
    div.querySelector('.btn-active').onclick = () => {
      accounts = readAccountsFromDom();
      activeMailbox = normalizeMailbox(div.querySelector('.f-mailbox').value) || activeMailbox;
      renderAccounts();
      updateActiveLabel();
      scheduleDraftSave();
    };
    div.querySelector('.btn-del').onclick = () => {
      accounts = readAccountsFromDom();
      const mb = normalizeMailbox(div.querySelector('.f-mailbox').value);
      accounts = accounts.filter(x => x.mailbox !== mb);
      if (!accounts.length) accounts = SEED.map(x => ({...x}));
      if (!accounts.some(x => x.mailbox === activeMailbox))
        activeMailbox = accounts[0].mailbox;
      renderAccounts();
      updateActiveLabel();
      scheduleDraftSave();
    };
  });
  updateActiveLabel();
}

function escapeAttr(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');
}

function updateActiveLabel() {
  const a = accounts.find(x => x.mailbox === activeMailbox) || accounts[0];
  el('active-label').textContent = a?.mailbox
    ? `${a.mailbox} · ${a.template_name} · ${a.feishu_table_name || a.feishu_table_id}`
    : '未配置（填写自己的 Gmail 和模板后保存）';
  const hint = el('active-ux-hint');
  if (hint) {
    hint.textContent = 'active=当前发件号（可轮转）。发完 Y / 表空 → 影刀式切下一号并停留；不会自动恢复上一号。共 '
      + accounts.length + ' 个账号。';
  }
}

function activeAccount() {
  return accounts.find(a => a.mailbox === activeMailbox) || accounts[0];
}

function payloadFromUi() {
  accounts = readAccountsFromDom();
  // keep active if mailbox edited on active card
  const activeNode = document.querySelector('.acct.active .f-mailbox');
  if (activeNode) activeMailbox = normalizeMailbox(activeNode.value) || activeMailbox;
  validateAccounts(accounts, activeMailbox);
  const a = activeAccount();
  const rsEnabled = !!(el('reply-scan-enabled') && el('reply-scan-enabled').checked);
  let rsInterval = Number(el('reply-scan-interval')?.value);
  if (!Number.isFinite(rsInterval)) rsInterval = 10;
  rsInterval = Math.max(3, Math.min(180, Math.round(rsInterval)));
  return {
    mailbox: a.mailbox,
    template_name: a.template_name,
    feishu_table_name: a.feishu_table_name,
    feishu_table_id: a.feishu_table_id,
    window_minutes: a.window_minutes,
    batch_size: a.batch_size,
    active_mailbox: activeMailbox,
    accounts: accounts.map(x => ({
      mailbox: x.mailbox,
      template_name: x.template_name,
      feishu_table_name: x.feishu_table_name,
      feishu_table_id: x.feishu_table_id,
      pace: {
        window_minutes: x.window_minutes,
        max_messages_per_window: x.batch_size,
        daily_limit: 500,
        min_gap_seconds: 90,
      },
      armed: true,
    })),
    switch_policy: {mode: 'finish_then_next', keep_test1_on_active: false, test_send: 'per_account_one'},
    reply_scan: {enabled: rsEnabled, interval_minutes: rsInterval},
  };
}

async function persistLocal(settings) {
  await chrome.storage.local.set({settings, accounts: settings.accounts, active_mailbox: settings.active_mailbox || settings.mailbox});
}

async function loadLocal() {
  const s = await chrome.storage.local.get(['settings', 'accounts', 'active_mailbox', 'draftSettings']);
  return s;
}

async function fetchQueueConfig() {
  const r = await fetch(ORIGIN + '/api/config', {signal: AbortSignal.timeout(4000)});
  if (!r.ok) throw Error('读取队列配置失败');
  return r.json();
}

async function pushQueueConfig(settings) {
  const r = await fetch(ORIGIN + '/api/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(settings),
    signal: AbortSignal.timeout(12000),
  });
  const data = await r.json();
  if (!r.ok) throw Error(data.detail || '写入队列配置失败');
  return data;
}

async function saveSettings({requireQueue = false} = {}) {
  const savedRevision = draftRevision;
  const settings = payloadFromUi();
  let effectiveSettings = settings;
  await persistLocal(settings);
  if (draftRevision === savedRevision) {
    clearTimeout(draftTimer);
    await chrome.storage.local.remove('draftSettings');
  }
  try {
    const prev = (await chrome.storage.local.get('config')).config || {};
    await chrome.storage.local.set({
      config: {
        ...prev,
        reply_scan: settings.reply_scan || {enabled: true, interval_minutes: 10},
      },
      replyScanSettings: settings.reply_scan || {enabled: true, interval_minutes: 10},
    });
    try { await chrome.runtime.sendMessage({type: 'reply-scan-settings', reply_scan: settings.reply_scan}); } catch {}
  } catch {}
  let queueNote = '队列离线：已只写入扩展本地；连接并启动会自动拉起队列';
  try {
    const data = await pushQueueConfig(settings);
    if (data.settings) {
      effectiveSettings = data.settings;
      await persistLocal(effectiveSettings);
    }
    queueNote = '已同步 config/one-mailbox.json · active=' + (data.mailbox || settings.mailbox)
      + ' · accounts=' + (settings.accounts || []).length;
    if (Array.isArray(data.settings?.accounts) && data.settings.accounts.length) {
      applyServerAccounts(data.settings);
    } else if (data.settings) {
      // flat fallback
      const i = accounts.findIndex(a => a.mailbox === data.settings.mailbox);
      if (i >= 0) {
        accounts[i] = {
          ...accounts[i],
          mailbox: data.settings.mailbox,
          template_name: data.settings.template_name,
          feishu_table_name: data.settings.feishu_table_name,
          feishu_table_id: data.settings.feishu_table_id,
          window_minutes: data.settings.window_minutes,
          batch_size: data.settings.batch_size,
        };
        activeMailbox = data.settings.mailbox;
        renderAccounts();
      }
    }
  } catch (e) {
    if (requireQueue) throw Error('本地队列离线或写入失败：' + e.message + '。连接并启动应自动拉起；若仍失败请双击「一点即运行」');
    queueNote = '队列未同步（' + e.message + '）；扩展本地已保存';
  }
  return {settings: effectiveSettings, queueNote};
}

function numAllowZero(v, fallback) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}


function applyReplyScanUi(settings) {
  const rs = (settings && settings.reply_scan && typeof settings.reply_scan === 'object')
    ? settings.reply_scan
    : {enabled: true, interval_minutes: 10};
  const en = el('reply-scan-enabled');
  const iv = el('reply-scan-interval');
  if (en) en.checked = rs.enabled !== false;
  if (iv) iv.value = String(Math.max(3, Math.min(180, Number(rs.interval_minutes) || 10)));
}

function applyServerAccounts(settings) {
  applyReplyScanUi(settings);
  const list = settings.accounts || [];
  if (!list.length) return;
  accounts = list.map(a => {
    const pace = (a && a.pace && typeof a.pace === 'object') ? a.pace : {};
    const yRaw = (pace.max_messages_per_window != null && pace.max_messages_per_window !== '')
      ? pace.max_messages_per_window
      : a.batch_size;
    const xRaw = (pace.window_minutes != null && pace.window_minutes !== '')
      ? pace.window_minutes
      : a.window_minutes;
    return {
      mailbox: normalizeMailbox(a.mailbox),
      template_name: (a.template_name || '').trim(),
      feishu_table_name: (a.feishu_table_name || '').trim(),
      feishu_table_id: (a.feishu_table_id || '').trim(),
      window_minutes: Math.max(1, numAllowZero(xRaw, 30)),
      batch_size: Math.max(0, numAllowZero(yRaw, 15)),
    };
  });
  activeMailbox = normalizeMailbox(settings.active_mailbox || settings.mailbox) || accounts[0].mailbox;
  renderAccounts();
}


const NATIVE_HOST = 'com.tk.outreach.sendqueue';
const POLL_MS = 10000;
const ROUND_TIMEOUT_MS = 8000;
const HEALTH_TIMEOUT_MS = 3000;

let lastGoodRound = null;
let roundFailStreak = 0;
let refreshInFlight = false;
let ensureInFlight = null;

function formatEtaDisplay(j) {
  if (!j) return '—';
  if (j.accounts_count === 0) return '未配置发信账号；请填写 Gmail、模板和飞书表后保存';
  if (j.armed === false) return '发信已锁定（armed=false）；恢复后重算';
  if (j.paused === true || j.next_round_eta_label === '已暂停' || j.next_round_eta_human === '已暂停') {
    return '已暂停';
  }
  const human = j.next_round_eta_human || '';
  const wall = j.next_round_eta || '';
  const label = j.next_round_eta_label || '';
  if (human && wall) return human + ' · ' + wall;
  if (human) return human;
  if (wall && label) return wall + ' · ' + label;
  if (wall) return wall;
  if (label) return label;
  return '—';
}

async function isManualPaused() {
  try {
    const s = await chrome.storage.local.get('config');
    return !!(s.config && (s.config.manualPaused || s.config.enabled === false));
  } catch {
    return false;
  }
}

async function applyRoundPayload(j) {
  const eta = el('next-eta');
  const count = el('next-count');
  const rem = el('remaining');
  const note = el('round-note');
  const sched = el('accounts-schedule');
  const summary = el('schedule-summary');
  if (!eta) return;
  const localPaused = await isManualPaused();
  const unconfigured = j?.accounts_count === 0;
  const locked = j?.armed === false && !unconfigured;
  const paused = !!(j && (j.paused === true || localPaused || locked || unconfigured));
  const pauseLabel = unconfigured ? '未配置发信账号；请填写 Gmail、模板和飞书表后保存' :
    (locked ? '发信已锁定（armed=false）；恢复后重算' : '已暂停');
  const view = paused ? {
    ...j,
    paused: true,
    next_round_eta: null,
    next_round_eta_ts: null,
    next_round_eta_label: pauseLabel,
    next_round_eta_human: pauseLabel,
    next_round_count: 0,
    next_round_count_note: '已暂停',
    combined: {
      ...(j.combined || {}),
      next_after_active_eta: null,
    },
    accounts_schedule: (Array.isArray(j.accounts_schedule) ? j.accounts_schedule : []).map(row => ({
      ...row,
      paused: true,
      next_round_eta: null,
      next_round_eta_ts: null,
      next_round_eta_label: pauseLabel,
      next_round_eta_human: pauseLabel,
      next_round_count: 0,
      next_round_count_note: '已暂停',
    })),
  } : j;
  eta.textContent = formatEtaDisplay(view);
  count.textContent = paused
    ? pauseLabel
    : (String(view.next_round_count ?? 0) + ' 人'
      + (view.next_round_count_note ? '（' + view.next_round_count_note + '）' : ''));
  rem.textContent = unconfigured ? '—（尚未配置账号）' : view.feishu_error && view.remaining_unsent == null
    ? ('读取失败：' + view.feishu_error)
    : String(view.remaining_unsent ?? '—') + (view.remaining_stale ? '（缓存）' : '');
  const rows = Array.isArray(view.accounts_schedule) ? view.accounts_schedule : [];
  if (sched) {
    if (!rows.length) {
      sched.textContent = '（尚无账号排程；请保存双账号）';
    } else {
      sched.innerHTML = rows.map(row => {
        const badge = row.is_active ? '<span class="badge">当前发件号</span>' : '';
        const yOff = !!(row.y_disabled || (row.pace && Number(row.pace.max_messages_per_window) === 0));
        const etaLine = yOff
          ? '已禁用（Y=0）'
          : (paused || row.paused
            ? pauseLabel
            : ((row.next_round_eta_human || '')
              + (row.next_round_eta ? ((row.next_round_eta_human ? ' · ' : '') + row.next_round_eta) : '')
              + (!row.next_round_eta_human && row.next_round_eta_label ? (' · ' + row.next_round_eta_label) : '')));
        const remLine = (row.feishu_error && row.remaining_unsent == null)
          ? ('读取失败：' + row.feishu_error)
          : (String(row.remaining_unsent ?? '—') + (row.remaining_stale ? '（缓存）' : ''));
        return '<div class="sched-acct' + (row.is_active ? ' active' : '') + '">'
          + '<div class="who">' + escapeAttr(row.mailbox || '') + ' ' + badge + '</div>'
          + '<div>模板：' + escapeAttr(row.template_name || '') + '</div>'
          + '<div>下一批：<strong>' + escapeAttr(etaLine || '—') + '</strong></div>'
          + '<div>下一批最多：<strong>' + (yOff ? '已禁用/Y=0' : (paused || row.paused ? pauseLabel : String(row.next_round_count ?? 0))) + (yOff || paused ? '' : ' 人') + '</strong>'
          + (((yOff || (!paused && !row.paused)) && row.next_round_count_note) ? ('（' + escapeAttr(row.next_round_count_note) + '）') : '') + '</div>'
          + '<div>剩余未发：<strong>' + escapeAttr(remLine) + '</strong></div>'
          + ((row.test_pending || row.schedule_pending || row.pending_jobs)
            ? ('<div>队列：'
              + (row.test_pending ? ('<strong>即将发/正在发 ' + row.test_pending + '</strong>（立即发送·每号1封密送Y人·不等切号/不等节奏）') : '')
              + (row.test_pending && (row.schedule_pending || (row.pending_jobs && !row.test_pending)) ? ' · ' : '')
              + (row.active_schedule ? ('定时处理中 ' + row.active_schedule) : '')
              + (row.schedule_pending > (row.active_schedule || 0)
                ? ((row.active_schedule ? ' · ' : '') + '定时待发 ' + (row.schedule_pending - (row.active_schedule || 0)))
                : (!row.test_pending && !row.active_schedule && row.pending_jobs ? ('pending ' + row.pending_jobs) : ''))
              + ' <span style="opacity:.75">≠ 上方「下一批最多」</span></div>')
            : '')
          + '</div>';
      }).join('');
    }
  }
  if (summary) {
    const c = view.combined || {};
    summary.textContent = [
      paused ? ('状态：' + pauseLabel + '（当前没有下一封时间）') : '',
      '合计剩余未发：' + (c.total_remaining_unsent ?? '—'),
      '账号数：' + (c.accounts ?? view.accounts_count ?? rows.length),
      '当前发件：' + (c.active_mailbox || view.mailbox || activeMailbox),
      paused ? '' : (c.rotation_note || view.active_ux_note || ''),
      (!paused && c.next_after_active_eta) ? ('预计切到下一号时间：' + c.next_after_active_eta) : '',
      view.test_pending ? ('即将发/正在发：' + view.test_pending + '（立即发送·每号1封密送Y人·多标签页各发本号）') : '',
      view.active_schedule ? ('定时处理中：' + view.active_schedule) : '',
      view.schedule_pending > (view.active_schedule || 0)
        ? ('定时待发：' + (view.schedule_pending - (view.active_schedule || 0)))
        : (view.pending_jobs && !view.test_pending && !view.active_schedule ? ('队列 pending：' + view.pending_jobs) : ''),
      !paused && view.schedule_next_count != null ? ('下一批最多：' + view.schedule_next_count + ' 人') : '',
      view.schedule_note || '',
    ].filter(Boolean).join(' · ');
  }
  if (note) {
    note.textContent = [
      paused ? (pauseLabel + '：当前没有下一封时间') : '',
      '当前发件号=' + (view.mailbox || activeMailbox),
      paused ? '已暂停' : (view.next_round_eta_human || ''),
      view.active_ux_note || '',
      view.switch_policy ? ('策略：' + (view.switch_policy.mode || '')) : '',
      view.switched_to ? ('刚切换并停留在：' + view.switched_to) : '',
      view.test_pending ? ('测发pending：' + view.test_pending) : '',
      (view.schedule_pending || (view.pending_jobs && !view.test_pending)) ? ('定时/pending：' + (view.schedule_pending || view.pending_jobs)) : '',
      (!paused && view.pace_reason && view.pace_reason !== 'ok') ? ('节奏：' + view.pace_reason) : '',
      '发信不依赖本排程接口',
    ].filter(Boolean).join(' ');
  }
}

async function refreshRound() {
  const eta = el('next-eta');
  if (!eta) return;
  try {
    const r = await fetch(ORIGIN + '/api/round-status', {signal: AbortSignal.timeout(ROUND_TIMEOUT_MS)});
    const j = await r.json();
    if (!r.ok) throw Error(j.detail || 'round-status 失败');
    lastGoodRound = j;
    roundFailStreak = 0;
    await applyRoundPayload(j);
    if (j.mailbox && j.mailbox !== activeMailbox) {
      activeMailbox = j.mailbox;
      renderAccounts();
      updateActiveLabel();
      try {
        const local = await chrome.storage.local.get(['settings', 'config']);
        const settings = {...(local.settings || {}), active_mailbox: j.mailbox, mailbox: j.mailbox};
        const config = local.config ? {...local.config, sender: j.mailbox} : local.config;
        await chrome.storage.local.set({settings, active_mailbox: j.mailbox, ...(config ? {config} : {})});
      } catch {}
    }
  } catch (e) {
    roundFailStreak += 1;
    // Keep last good — do NOT blank to 离线 on one failed fetch (flicker root cause)
    if (lastGoodRound) {
      await applyRoundPayload(lastGoodRound);
      const note = el('round-note');
      if (note) note.textContent = (note.textContent || '') + ' · 排程刷新暂缓（' + (e.message || e) + '，保留上次）';
    } else {
      // Only first paints without cache show soft waiting — not hard 离线
      if (!eta.textContent || eta.textContent === '读取中…') {
        eta.textContent = '排程读取中（不影响发信）';
      }
      const summary = el('schedule-summary');
      if (summary && (!summary.textContent || summary.textContent.includes('读取'))) {
        summary.textContent = '排程暂缓：' + (e.message || e) + '（发信路径不依赖此接口）';
      }
    }
    // Toast only on sustained failure
    if (roundFailStreak === 3) {
      showToast('排程连续失败（队列/飞书慢）。发信不受影响；已保留上次排程。', 'info', 8000);
    }
  }
}

async function probeHealth() {
  const r = await fetch(ORIGIN + '/api/health', {signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS)});
  const j = await r.json();
  if (!r.ok || !j.ok) throw Error(j.detail || 'health 异常');
  return j;
}

function nativeCommand(command) {
  return new Promise((resolve) => {
    let port;
    try {
      port = chrome.runtime.connectNative(NATIVE_HOST);
    } catch (e) {
      resolve({ok: false, error: String(e), via: 'native'});
      return;
    }
    let done = false;
    const finish = (obj) => {
      if (done) return;
      done = true;
      try { port.disconnect(); } catch {}
      resolve(obj);
    };
    const timer = setTimeout(() => finish({ok: false, error: 'native timeout', via: 'native'}), 70000);
    port.onMessage.addListener((msg) => {
      clearTimeout(timer);
      finish({...(msg || {}), via: 'native'});
    });
    port.onDisconnect.addListener(() => {
      const err = chrome.runtime.lastError && chrome.runtime.lastError.message;
      clearTimeout(timer);
      finish({ok: false, error: err || 'native disconnected', via: 'native'});
    });
    try {
      port.postMessage({cmd: command});
    } catch (e) {
      clearTimeout(timer);
      finish({ok: false, error: String(e), via: 'native'});
    }
  });
}

const nativeEnsureQueue = () => nativeCommand('start');

function protocolEnsureQueue() {
  // Custom protocol registered by Install-NativeHost.ps1
  try {
    const a = document.createElement('a');
    a.href = 'tk-send-queue://ensure';
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    a.remove();
    return {ok: true, via: 'protocol'};
  } catch (e) {
    return {ok: false, error: String(e), via: 'protocol'};
  }
}

async function ensureLocalQueue({force = false} = {}) {
  if (ensureInFlight) return ensureInFlight;
  ensureInFlight = (async () => {
    try {
      const h = await probeHealth();
      return {ok: true, already: true, health: h};
    } catch {}
    showToast('本机队列未在线，正在自动启动…', 'info', 8000);
    let native = await nativeEnsureQueue();
    if (native && native.ok) {
      for (let i = 0; i < 20; i++) {
        try { return {ok: true, started: true, health: await probeHealth(), via: native.via}; }
        catch { await new Promise(r => setTimeout(r, 300)); }
      }
    }
    protocolEnsureQueue();
    for (let i = 0; i < 25; i++) {
      try { return {ok: true, started: true, health: await probeHealth(), via: 'protocol'}; }
      catch { await new Promise(r => setTimeout(r, 400)); }
    }
    return {
      ok: false,
      error: (native && native.error) || '无法自动启动队列',
      hint: '请确认已安装 Native Host（一点即运行会注册），或手动双击「一点即运行-发信插件.cmd」',
    };
  })();
  try { return await ensureInFlight; }
  finally { ensureInFlight = null; }
}

async function refresh() {
  if (refreshInFlight) return;
  refreshInFlight = true;
  try {
    const s = await chrome.storage.local.get(['config', 'current', 'lastResult', 'lastError',
      'replyRelayOutbox', 'replyRelayLastResult']);
    const a = activeAccount() || {};
    let health = '';
    try {
      const j = await probeHealth();
      health = '队列：' + (j.ok ? '在线' : '异常')
        + ' · armed=' + j.armed
        + ' · active=' + (j.mailbox || '-')
        + ' · template=' + (j.template_name || '(空)')
        + ' · accounts=' + (j.accounts_count ?? accounts.length)
        + ' · feishu=' + ((j.feishu && (j.feishu.table_name || j.feishu.table_id)) || '-')
        + ' · 一次最多=' + ((j.pace && (j.pace.max_messages_per_window + '人 / ' + j.pace.window_minutes + '分钟一批')) || '-')
        + ' · pending=' + j.pending
        + (j.pending_writebacks ? ' · 飞书待写回=' + j.pending_writebacks : '')
        + ' · switch=' + (j.switch_policy || 'finish_then_next')
        + (j.feishu_config_error ? ' · 飞书配置错误：' + j.feishu_config_error : '');
    } catch {
      health = '队列：离线（将自动尝试启动；也可双击「一点即运行」）';
    }
    const block = [
      health,
      '表单 active：' + (a.mailbox || '-') + ' / ' + (a.template_name || '-')
        + ' / 飞书 ' + (a.feishu_table_name || a.feishu_table_id || '-')
        + ' / 一次最多 ' + (a.batch_size == null ? '-' : a.batch_size) + ' 人' + (Number(a.batch_size) === 0 ? '（暂时禁用）' : '') + '，批间隔 ' + (a.window_minutes || '-') + ' 分钟'
        + ' · 共 ' + accounts.length + ' 个账号',
      s.config?.enabled ? '扩展发送：已启动' : '扩展发送：已暂停',
      (() => { const rs = s.config?.reply_scan || s.replyScanSettings || {}; const on = rs.enabled !== false; const m = Number(rs.interval_minutes) || 10; return '定期查回信：' + (on ? ('开 · 每 ' + m + ' 分钟') : '关'); })(),
      REPLY_RELAY_URL && REPLY_RELAY_KEY ?
        ('飞书群播报：中心端 · 待确认 ' + Object.keys(s.replyRelayOutbox || {}).length
        + ' 条 · 最近 ' + (s.replyRelayLastResult?.status || '尚无回执')
        + (s.replyRelayLastResult?.error ? ('（' + s.replyRelayLastResult.error + '）') : '')) : '',
      s.config?.sender ? '发件邮箱（会话）：' + s.config.sender : '',
      s.current ? '当前任务：' + s.current.job.recipient + ' · ' + (s.current.stage || s.current.phase) : '等待队列任务',
      s.lastResult?.message,
      s.lastError ? '最近提示：' + s.lastError.message : '',
    ].filter(Boolean).join('\n\n');
    if (Date.now() < toastStickyUntil && el('toast')?.classList.contains('show')) {
      el('status').textContent = el('toast').textContent + '\n\n' + block;
    } else {
      el('status').textContent = block;
    }
    await refreshRound();
  } finally {
    refreshInFlight = false;
  }
}

async function api(token, name, body) {
  const r = await fetch(ORIGIN + '/api/browser-mail/worker/' + name, {
    method: 'POST',
    headers: {'Content-Type': 'application/json', Authorization: 'Bearer ' + token},
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(12000),
  });
  const data = await r.json();
  if (!r.ok) throw Error(data.detail || '队列没有确认');
  return data;
}

el('add-account').onclick = () => {
  accounts = readAccountsFromDom();
  accounts.push({
    mailbox: '',
    template_name: '',
    feishu_table_name: '',
    feishu_table_id: '',
    window_minutes: 30,
    batch_size: 15,
  });
  renderAccounts();
  scheduleDraftSave();
};

el('save').onclick = async () => {
  let draftSaved = false;
  try {
    await persistDraft();
    draftSaved = true;
    await ensureLocalQueue();
    const {queueNote} = await saveSettings({requireQueue: false});
    el('status').textContent = '参数已保存。\n' + queueNote;
    await refresh();
  } catch (e) {
    el('status').textContent = (draftSaved ? '本地草稿已保存；' : '') + e.message;
  }
};

el('connect').onclick = async () => {
  try {
    await persistDraft();
    const ensured = await ensureLocalQueue({force: true});
    if (!ensured.ok) {
      throw Error('本机队列未能自动启动：' + (ensured.error || '') + '。' + (ensured.hint || ''));
    }
    let old = await chrome.storage.local.get(['config', 'current', 'client_id']);
    const freshAfterPause = old.config?.manualPaused === true;
    if (old.current) {
      showToast('正在结束上次未完成的邮件任务…', 'info', 12000);
      try { await chrome.runtime.sendMessage({type: 'wake'}); } catch {}
      for (let i = 0; i < 24 && old.current; i++) {
        await new Promise(resolve => setTimeout(resolve, 500));
        old = await chrome.storage.local.get(['config', 'current', 'client_id']);
      }
      if (old.current)
        throw Error('上次邮件尚待 Gmail 已发记录核对；当前任务不会重复发送。请保持该账号 Gmail 页签可用。');
    }
    const {settings, queueNote} = await saveSettings({requireQueue: true});
    if (freshAfterPause) {
      const resetResponse = await fetch(ORIGIN + '/api/reset-unsent-for-fresh-start', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}',
        signal: AbortSignal.timeout(12000),
      });
      const resetData = await resetResponse.json();
      if (!resetResponse.ok) throw Error(resetData.detail || '旧待发任务清理失败');
      await chrome.storage.local.set({lastResult: null, lastError: null});
    }
    const response = await fetch(ORIGIN + '/api/browser-mail/extension-bootstrap', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({}),
      signal: AbortSignal.timeout(12000),
    });
    const data = await response.json();
    if (!response.ok) throw Error(data.detail || '连接失败');
    const client_id = old.client_id || crypto.randomUUID();
    const config = {
      token: data.token,
      account_id: data.account_id || 'multi-active',
      sender: data.sender || settings.active_mailbox || settings.mailbox,
      template_name: data.template_name || settings.template_name,
      enabled: true,
      manualPaused: false,
      reply_scan: settings.reply_scan || {enabled: true, interval_minutes: 10},
    };
    await api(config.token, 'state', {enabled: true});
    await chrome.storage.local.set({config, client_id, settings, lastError: null});
    try { await chrome.runtime.sendMessage({type: 'heal'}); } catch {
      try { await chrome.runtime.sendMessage({type: 'wake'}); } catch {}
    }
    const etaHint = lastGoodRound?.next_round_eta_human || '排程见下方面板';
    showToast('已连接并启动 active=' + (config.sender || settings.mailbox) + ' · ' + etaHint, 'ok', 10000);
    el('status').textContent = '已连接并启动 active。\n' + queueNote + '\n' + etaHint;
    await refresh();
  } catch (e) {
    el('status').textContent = e.message;
    showToast(String(e.message || e), 'err', 12000);
  }
};

el('restart').onclick = async () => {
  const button = el('restart');
  button.disabled = true;
  showToast('正在重启本地队列并应用源码更新…', 'info', 20000);
  try {
    const result = await nativeCommand('restart');
    if (!result?.ok) throw Error(result?.hint || result?.error || '重启失败');
    if (result.center_warning) throw Error('发信后台已重启，但飞书中心旧进程未退出：' + result.center_warning);
    showToast('后台已重启，正在重新加载插件…', 'ok', 10000);
    chrome.runtime.reload();
  } catch (error) {
    showToast('后台重启失败：' + (error.message || error), 'err', 20000);
    button.disabled = false;
  }
};

el('send-now').onclick = async () => {
  const btn = el('send-now');
  btn.disabled = true;
  showToast('正在自动确保队列并按账号入队（每号1封密送Y人）…', 'info', 8000);
  try {
    const ensured = await ensureLocalQueue();
    if (!ensured.ok) throw Error('队列未启动：' + (ensured.error || ''));
    try { await saveSettings({requireQueue: false}); } catch (e) {
      console.warn('save before enqueue', e);
    }
    let healInfo = null;
    try {
      healInfo = await healExtensionSession();
      showToast('会话已刷新（active=' + (healInfo.mailbox || activeMailbox) + '，pending=' + (healInfo.pending ?? '?') + '），正在入队…', 'info', 8000);
    } catch (e) {
      showToast('会话 heal 失败：' + e.message + ' — 仍尝试入队', 'err', 12000);
    }
    const r = await fetch(ORIGIN + '/api/enqueue-test', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({count: 1, reason: 'test'}),
      signal: AbortSignal.timeout(60000),
    });
    const data = await r.json();
    if (!r.ok) throw Error(data.detail || '入队失败');
    const rows = (data.enqueued || []).map(x => (x.mailbox || '?') + '->BCC×' + (x.people_count || (x.recipients || []).length || 1) + '(' + (x.recipient || '?') + '…)').join(' | ') || '(无)';
    const skipped = (data.skipped || []).map(x => (x.mailbox || '?') + '=' + (x.reason || '?')).join(' | ');
    const mb = data.last_mailbox || data.mailbox || activeMailbox;
    const msg = 'OK 已按账号入队密送 ' + (data.count ?? (data.enqueued || []).length) + ' 封 / 共 ' + (data.people_count ?? '?') + ' 人（每号1封BCC=Y人；跳过 ' + (data.accounts_skipped ?? (data.skipped || []).length) + '）\n'
      + rows
      + (skipped ? ('\n跳过：' + skipped) : '')
      + ' · pending=' + (data.pending_after ?? '?')
      + ' · 将停留末号 ' + mb
      + '\n' + (data.note || '多标签页：各 Gmail 页签领本号任务并立即发送（不等切号/节奏）；发信→飞书写回→收件箱扫描');
    showToast(msg, 'ok', 15000);
    el('status').textContent = msg;
    await chrome.storage.local.set({lastError: null, lastResult: {at: Date.now(), message: msg}});
    try { await chrome.runtime.sendMessage({type: 'wake'}); } catch {}
    await refresh();
  } catch (e) {
    const err = '立即发送失败：' + e.message;
    showToast(err, 'err', 20000);
    el('status').textContent = err;
  } finally {
    btn.disabled = false;
  }
};

el('pause').onclick = async () => {
  const s = await chrome.storage.local.get('config');
  await chrome.storage.local.set({config: {...s.config, enabled: false, manualPaused: true}});
  // Immediately clear ETA countdown in UI (do not wait for poll).
  const eta = el('next-eta');
  const count = el('next-count');
  if (eta) eta.textContent = '已暂停';
  if (count) count.textContent = '已暂停';
  if (lastGoodRound) {
    lastGoodRound = {
      ...lastGoodRound,
      paused: true,
      next_round_eta: null,
      next_round_eta_ts: null,
      next_round_eta_label: '已暂停',
      next_round_eta_human: '已暂停',
      next_round_count: 0,
      next_round_count_note: '已暂停',
    };
    await applyRoundPayload(lastGoodRound);
  }
  showToast('已暂停本机发送', 'ok', 6000);
  try {
    if (s.config?.token) await api(s.config.token, 'state', {enabled: false});
    await refresh();
  } catch (e) {
    el('status').textContent = '本机已暂停；后台尚未确认暂停。' + e.message;
  }
};




async function boot() {
  if (REPLY_RELAY_URL && REPLY_RELAY_KEY) {
    if (el('reply-relay-mode')) el('reply-relay-mode').style.display = '';
  }
  let loaded = null;
  let ensured = null;
  try { ensured = await ensureLocalQueue(); } catch {}
  if (ensured?.ok) {
    nativeCommand('update').then(result => {
      if (result?.updated) {
        const msg = '已从 GitHub 下载 ' + result.after + '；点「应用更新并重启后台」即可生效。';
        el('update-hint').textContent = msg;
        showToast(msg, 'info', 30000);
      } else if (result?.ok === false) {
        el('update-hint').textContent = 'GitHub 更新检查未成功：' + (result.error || '请查看 data/logs/team-update.log');
      } else if (result?.ok) {
        el('update-hint').textContent = '已检查 GitHub：当前为最新版本 ' + chrome.runtime.getManifest().version;
      }
    }).catch(() => {});
  }
  const local = await loadLocal();
  let queueSettings = null;
  try {
    const q = await fetchQueueConfig();
    if (q?.settings) queueSettings = q.settings;
  } catch {}
  const initial = chooseInitialSettings(queueSettings, local);
  loaded = initial.settings;
  if (initial.restoredLocal) {
    el('update-hint').textContent = initial.restoredDraft ?
      '已恢复上次未填完的本地草稿；填齐后点「保存参数」。' :
      '已恢复扩展本地保存的账号；队列尚未配置，请点「保存参数」同步。';
  }
  if (loaded && Array.isArray(loaded.accounts) && loaded.accounts.length) {
    applyServerAccounts(loaded);
  } else if (loaded && loaded.mailbox) {
    accounts = [{
      mailbox: normalizeMailbox(loaded.mailbox),
      template_name: loaded.template_name || '',
      feishu_table_name: loaded.feishu_table_name || '',
      feishu_table_id: loaded.feishu_table_id || '',
      window_minutes: loaded.window_minutes || 30,
      batch_size: Math.max(0, numAllowZero(loaded.batch_size, 15)),
    }];
    activeMailbox = normalizeMailbox(loaded.mailbox);
    renderAccounts();
  } else {
    accounts = SEED.map(s => ({...s}));
    if (loaded) {
      accounts[0].feishu_table_name = loaded.feishu_table_name || '';
      accounts[0].feishu_table_id = loaded.feishu_table_id || '';
    }
    activeMailbox = '';
    renderAccounts();
  }
  applyReplyScanUi(loaded || {reply_scan: {enabled: true, interval_minutes: 10}});
  await refresh();
}

boot();
el('accounts').addEventListener('input', scheduleDraftSave);
el('reply-scan-enabled').addEventListener('change', scheduleDraftSave);
el('reply-scan-interval').addEventListener('input', scheduleDraftSave);
setInterval(refresh, POLL_MS);
