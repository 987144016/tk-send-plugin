import assert from 'node:assert/strict';
import {pausedPreSendReceipt, sendingStateAfterHeal} from '../extension/pause-recovery.mjs';

const config = {enabled: false, token: 'local-token'};
assert.equal(pausedPreSendReceipt({phase: 'preparing'}, config)?.status, 'paused');
assert.equal(pausedPreSendReceipt({phase: 'prepared'}, config)?.status, 'paused');
assert.equal(pausedPreSendReceipt({phase: 'clicked'}, config), null);
assert.equal(pausedPreSendReceipt({phase: 'click_authorized'}, config), null);
assert.equal(pausedPreSendReceipt({phase: 'preparing'}, {...config, enabled: true}), null);
assert.deepEqual(sendingStateAfterHeal({manualPaused: true}, {enabled: true}),
  {enabled: false, manualPaused: true}, 'refreshing a token never resumes a user pause');
assert.deepEqual(sendingStateAfterHeal({enabled: true}, {enabled: false}),
  {enabled: false, manualPaused: true}, 'queue pause overrides stale extension sending state');
assert.deepEqual(sendingStateAfterHeal({enabled: true}, {enabled: true}),
  {enabled: true, manualPaused: false}, 'a running queue remains running after token refresh');

console.log('a user-paused pre-send draft is released without retrying a clicked message');
