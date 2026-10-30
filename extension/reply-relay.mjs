const eventKey = (mailbox, reply) => mailbox.toLowerCase() + '|' + (reply.message_id || '').toLowerCase();
const candidate = reply => reply?.body_verified === true
  && /^[a-f0-9]{10,40}$/i.test(reply.message_id || '')
  && !reply.bounce_kind
  && !/mailer-daemon|postmaster|no.?reply|notification@/i.test(reply.from || '');

export async function captureReplyEvents(storage, mailbox, scan, tableId = '') {
  const bag = await storage.get(['replyRelayOutbox', 'replyRelaySeen', 'replyRelayBaseline']);
  const outbox = {...(bag.replyRelayOutbox || {})};
  const seen = {...(bag.replyRelaySeen || {})};
  const baseline = {...(bag.replyRelayBaseline || {})};
  const rows = (scan?.replies || []).filter(r => /^[a-f0-9]{10,40}$/i.test(r?.message_id || ''));
  if (!baseline[mailbox]) {
    for (const r of rows) seen[eventKey(mailbox, r)] = Date.now();
    baseline[mailbox] = Date.now();
    await storage.set({replyRelaySeen: seen, replyRelayBaseline: baseline});
    return {queued: 0, baseline: true};
  }
  let queued = 0;
  for (const r of rows) {
    const key = eventKey(mailbox, r);
    if (!candidate(r) || seen[key] || outbox[key]) continue;
    outbox[key] = {mailbox, table_id: tableId, reply: {
      from: r.from, subject: r.subject, snippet: r.snippet,
      at: r.at, message_id: r.message_id, thread_id: r.thread_id,
      thread_url: r.thread_url, body: r.body, body_verified: true,
      suspicious: !!r.suspicious, spam: !!r.spam, unread: !!r.unread,
    }, captured_at: new Date().toISOString(), next_at: 0};
    seen[key] = Date.now();
    queued++;
  }
  if (queued) await storage.set({replyRelayOutbox: outbox, replyRelaySeen: seen});
  return {queued, baseline: false};
}

export async function flushReplyEvents(storage, relayUrl, relayKey, fetcher = fetch) {
  if (!relayUrl || !relayKey) return {status: 'not_configured'};
  const bag = await storage.get(['replyRelayOutbox']);
  const outbox = {...(bag.replyRelayOutbox || {})};
  const key = Object.keys(outbox).find(k => (outbox[k].next_at || 0) <= Date.now());
  if (!key) return {status: 'idle', pending: Object.keys(outbox).length};
  const item = outbox[key];
  try {
    const response = await fetcher(relayUrl.replace(/\/$/, '') + '/events', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', Authorization: 'Bearer ' + relayKey},
      body: JSON.stringify({mailbox: item.mailbox, table_id: item.table_id, reply: item.reply}),
      signal: AbortSignal.timeout(100000),
    });
    const result = await response.json();
    if (response.ok && result.ok === true &&
        (result.status === 'historical_skipped' ||
         (['sent', 'duplicate'].includes(result.status) && result.feishu_message_id))) {
      delete outbox[key];
      await storage.set({replyRelayOutbox: outbox,
        replyRelayLastResult: {at: Date.now(), status: result.status,
          gmail_message_id: item.reply.message_id,
          feishu_message_id: result.feishu_message_id}});
      return {status: 'confirmed', pending: Object.keys(outbox).length};
    }
    item.next_at = Date.now() + (response.status === 409 ? 600000 : 60000);
    item.last_error = result.error || 'not_confirmed';
  } catch (error) {
    item.next_at = Date.now() + 60000;
    item.last_error = error?.name || 'network_error';
  }
  outbox[key] = item;
  await storage.set({replyRelayOutbox: outbox,
    replyRelayLastResult: {at: Date.now(), status: 'pending', error: item.last_error}});
  return {status: 'pending', pending: Object.keys(outbox).length, error: item.last_error};
}
