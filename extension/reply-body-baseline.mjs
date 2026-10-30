const replyKey = (mailbox, id) => mailbox.toLowerCase() + '|' + id.toLowerCase();

export async function newReplyBodyCandidates(storage, mailbox, replies) {
  const bag = await storage.get(['replyBodyBaseline', 'replyBodySeen']);
  const baseline = {...(bag.replyBodyBaseline || {})};
  const seen = {...(bag.replyBodySeen || {})};
  const rows = (replies || []).filter(r => /^[a-f0-9]{10,40}$/i.test(r?.message_id || ''));
  if (!baseline[mailbox]) {
    for (const row of rows) seen[replyKey(mailbox, row.message_id)] = Date.now();
    baseline[mailbox] = Date.now();
    await storage.set({replyBodyBaseline: baseline, replyBodySeen: seen});
    return [];
  }
  return rows.filter(row => !seen[replyKey(mailbox, row.message_id)]);
}

export async function markReplyBodyRead(storage, mailbox, messageId) {
  const bag = await storage.get(['replyBodySeen']);
  const seen = {...(bag.replyBodySeen || {})};
  seen[replyKey(mailbox, messageId)] = Date.now();
  await storage.set({replyBodySeen: seen});
}
