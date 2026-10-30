export function mayReloadBeforeCompose(current) {
  const phase = current?.phase || '';
  if (['prepared', 'permit_requested', 'click_authorized', 'clicked',
       'searching', 'uncertain'].includes(phase)) return false;
  if (phase === 'preparing' &&
      /写信|草稿|收件人|密送|模板|正文|附件|图片/i.test(current?.stage || '')) return false;
  return true;
}
