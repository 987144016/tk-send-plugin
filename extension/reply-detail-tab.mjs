const KEY = 'replyDetailTab';
let inUse = false;

export async function cleanupReplyDetailTab(browser) {
  const previous = (await browser.storage.session.get(KEY))[KEY];
  if (!previous?.id) return false;
  let tab;
  try { tab = await browser.tabs.get(previous.id); }
  catch { await browser.storage.session.set({[KEY]: null}); return false; }
  if (!tab.url?.startsWith('https://mail.google.com/') || tab.active) {
    await browser.storage.session.set({[KEY]: null});
    return false;
  }
  try {
    await browser.tabs.remove(tab.id);
    await browser.storage.session.set({[KEY]: null, replyDetailCleanupError: null});
    return true;
  } catch (error) {
    await browser.storage.session.set({replyDetailCleanupError: error?.message || String(error)});
    return false;
  }
}

export async function withReplyDetailTab(browser, initialUrl, task) {
  if (inUse) return null;
  inUse = true;
  let tab = null;
  try {
    const previous = (await browser.storage.session.get(KEY))[KEY];
    if (previous?.id) {
      try {
        const old = await browser.tabs.get(previous.id);
        if (old.url?.startsWith('https://mail.google.com/')) tab = old;
      }
      catch { await browser.storage.session.set({[KEY]: null}); }
    }
    if (tab) {
      tab = await browser.tabs.update(tab.id, {url: initialUrl, active: false});
    } else {
      tab = await browser.tabs.create({url: initialUrl, active: false});
    }
    await browser.storage.session.set({[KEY]: {id: tab.id}});
    const navigate = async url => browser.tabs.update(tab.id, {url, active: false});
    return await task({tabId: tab.id, navigate});
  } finally {
    if (tab?.id) {
      try {
        await browser.tabs.remove(tab.id);
        await browser.storage.session.set({[KEY]: null, replyDetailCleanupError: null});
      } catch (error) {
        await browser.storage.session.set({[KEY]: {id: tab.id},
          replyDetailCleanupError: error?.message || String(error)});
      }
    }
    inUse = false;
  }
}
