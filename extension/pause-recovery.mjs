import {needsReconciliation} from './protocol.js';

export function sendingStateAfterHeal(previous = {}, queue = {}) {
  const manualPaused = previous.manualPaused === true || queue.enabled === false;
  return {enabled: !manualPaused, manualPaused};
}

export function pausedPreSendReceipt(current, config) {
  if (!current || !config?.token || config.enabled || needsReconciliation(current.phase)) return null;
  return {status: 'paused', error: '草稿阶段已暂停，尚未点击发送'};
}
