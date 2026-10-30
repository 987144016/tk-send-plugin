export function chooseInitialSettings(queueSettings, local = {}) {
  let saved = local.settings || null;
  if (Array.isArray(local.accounts) && local.accounts.length) {
    saved = {...(saved || {}), accounts: local.accounts,
      active_mailbox: local.active_mailbox || saved?.mailbox};
  }
  const queueHasAccount = !!(queueSettings &&
    ((Array.isArray(queueSettings.accounts) && queueSettings.accounts.length) || queueSettings.mailbox));
  const localHasAccount = !!(saved &&
    ((Array.isArray(saved.accounts) && saved.accounts.length) || saved.mailbox));
  if (!queueHasAccount && localHasAccount) return {settings: saved, restoredLocal: true};
  return {settings: queueSettings || saved, restoredLocal: false};
}
