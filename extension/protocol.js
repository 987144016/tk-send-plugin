// Standalone send-plugin protocol. Local queue only (port 8767). No FastMoss / Ziniao.
export const ORIGIN = 'http://127.0.0.1:8767';
export const BOOTSTRAP_KEY = '';

export function accountURL(sender) {
  if (!/^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@gmail\.com$/i.test(sender))
    throw Error('发件邮箱无效');
  return 'https://mail.google.com/mail/u/0/#inbox'; // no authuser URL (ban risk); 影刀-style DOM switch picks account
}

export function canClick(phase) {
  return phase === 'prepared';
}

export function needsReconciliation(phase) {
  return ['permit_requested', 'click_authorized', 'clicked', 'searching', 'uncertain'].includes(phase);
}

export function receiptMatches(job, r) {
  if (!(r?.status === 'sent' && r.sender === job.sender && r.recipient === job.recipient &&
        r.marker === job.marker && /^[a-f0-9]{10,40}$/.test(r.message_id) &&
        /^[a-f0-9]{10,40}$/.test(r.thread_id))) return false;
  if (job.recipient_mode !== 'bcc') return true;
  if (r.recipient_mode !== 'bcc' || !Array.isArray(r.recipients) || !Array.isArray(job.recipients)) return false;
  const expected = job.recipients.map(x => x.toLowerCase()).sort();
  const actual = r.recipients.map(x => x.toLowerCase()).sort();
  return actual.length === expected.length && new Set(actual).size === actual.length &&
         actual.every((x, i) => x === expected[i]);
}
