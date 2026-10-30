import assert from 'node:assert/strict';
import {mayReloadBeforeCompose} from '../extension/send-stall.mjs';

assert.equal(mayReloadBeforeCompose({phase:'preparing', stage:'正在展开 Gmail 密送栏，尚未发送'}), false);
assert.equal(mayReloadBeforeCompose({phase:'preparing', stage:'正在插入 Gmail 已保存模板'}), false);
assert.equal(mayReloadBeforeCompose({phase:'preparing', stage:'等待 Gmail 当前账号显示'}), true);
assert.equal(mayReloadBeforeCompose({phase:'prepared', stage:''}), false);

console.log('a generated compose draft is never reloaded by the stall watcher');
