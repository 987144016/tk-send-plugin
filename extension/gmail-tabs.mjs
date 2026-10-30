const KEY = 'gmailAccountProvision';
const GMAIL_URL = 'https://mail.google.com/mail/';

export async function ensureGmailAccountTabs(browser, accountEmails, readMailbox, switchAccount) {
  const wanted = [...new Set((accountEmails || [])
    .map(x => String(x || '').trim().toLowerCase())
    .filter(x => /^[^\s@]+@gmail\.com$/.test(x)))];
  if (!wanted.length) return {status: 'no_accounts'};
  const tabs = (await browser.tabs.query({url: 'https://mail.google.com/*'}))
    .filter(t => !t.discarded);
  const slots = [];
  for (const tab of tabs) {
    slots.push({...tab, mailbox: (await readMailbox(tab.id, tab.url || '', true)) || ''});
  }
  const state = (await browser.storage.local.get(KEY))[KEY];
  if (state?.target) {
    const target = slots.find(t => t.mailbox === state.target);
    if (target) {
      if (target.id !== state.tabId && state.created && slots.some(t => t.id === state.tabId))
        await browser.tabs.remove(state.tabId);
      await browser.storage.local.set({[KEY]: null});
      return {status: 'ready', mailbox: state.target};
    }
    const source = slots.find(t => t.id === state.tabId);
    if (!source) {
      try {
        const redirected = await browser.tabs.get(state.tabId);
        if (redirected?.url?.startsWith('https://accounts.google.com/'))
          return {status: 'attention', reason: 'gmail_login_required', mailbox: state.target};
      } catch {}
      await browser.storage.local.set({[KEY]: null});
      return {status: 'waiting', reason: 'provision_tab_closed'};
    }
    if (source.status !== 'complete') return {status: 'waiting', reason: 'gmail_loading'};
    if (state.rowClickedAt) {
      if (Date.now() - state.rowClickedAt < 15000)
        return {status: 'waiting', reason: 'account_switch_unconfirmed'};
      if ((state.attempts || 0) >= 2)
        return {status: 'attention', reason: 'account_switch_unconfirmed', mailbox: state.target};
      await browser.storage.local.set({[KEY]: {
        ...state, rowClickedAt: 0, retryAt: Date.now() + 30000,
      }});
      return {status: 'waiting', reason: 'account_switch_retry_scheduled'};
    }
    if (state.retryAt && Date.now() < state.retryAt)
      return {status: 'waiting', reason: 'account_switch_retry'};
    try {
      const result = await switchAccount(source.id, state.target);
      await browser.storage.local.set({[KEY]: {
        ...state,
        rowClickedAt: result?.account_row_clicked ? Date.now() : 0,
        attempts: (state.attempts || 0) + (result?.account_row_clicked ? 1 : 0),
        retryAt: result?.error ? Date.now() + 60000 : 0,
      }});
      return {status: 'waiting', reason: result?.stage || result?.error || 'account_switch'};
    } catch (error) {
      await browser.storage.local.set({[KEY]: {...state, retryAt: Date.now() + 60000}});
      return {status: 'waiting', reason: error?.message || 'account_switch_failed'};
    }
  }
  const missing = wanted.find(email => !slots.some(t => t.mailbox === email));
  if (!missing) return {status: 'ready'};
  if (tabs.length >= wanted.length) return {status: 'slot_limit', mailbox: missing};
  const tab = await browser.tabs.create({url: GMAIL_URL, active: false});
  await browser.storage.local.set({[KEY]: {
    tabId: tab.id, target: missing, created: true, rowClickedAt: 0, retryAt: 0,
  }});
  return {status: 'waiting', reason: 'gmail_tab_opened', mailbox: missing};
}
