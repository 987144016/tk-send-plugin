export function chooseInitialSettings(queueSettings, local = {}) {
  if (local.draftSettings && Array.isArray(local.draftSettings.accounts) &&
      local.draftSettings.accounts.length) {
    return {settings: local.draftSettings, restoredLocal: true, restoredDraft: true};
  }
  let saved = local.settings || null;
  if (Array.isArray(local.accounts) && local.accounts.length) {
    saved = {...(saved || {}), accounts: local.accounts,
      active_mailbox: local.active_mailbox || saved?.mailbox};
  }
  const queueHasAccount = !!(queueSettings &&
    ((Array.isArray(queueSettings.accounts) && queueSettings.accounts.length) || queueSettings.mailbox));
  const localHasAccount = !!(saved &&
    ((Array.isArray(saved.accounts) && saved.accounts.length) || saved.mailbox));
  if (!queueHasAccount && localHasAccount) return {settings: saved, restoredLocal: true, restoredDraft: false};
  return {settings: queueSettings || saved, restoredLocal: false, restoredDraft: false};
}
