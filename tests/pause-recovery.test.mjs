import assert from 'node:assert/strict';
import {pausedPreSendReceipt} from '../extension/pause-recovery.mjs';

const config = {enabled: false, token: 'local-token'};
assert.equal(pausedPreSendReceipt({phase: 'preparing'}, config)?.status, 'paused');
assert.equal(pausedPreSendReceipt({phase: 'prepared'}, config)?.status, 'paused');
assert.equal(pausedPreSendReceipt({phase: 'clicked'}, config), null);
assert.equal(pausedPreSendReceipt({phase: 'click_authorized'}, config), null);
assert.equal(pausedPreSendReceipt({phase: 'preparing'}, {...config, enabled: true}), null);

console.log('a user-paused pre-send draft is released without retrying a clicked message');
