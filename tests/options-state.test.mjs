import assert from 'node:assert/strict';
import {chooseInitialSettings} from '../extension/options-state.mjs';

const account = {mailbox: 'sender@gmail.com', template_name: 'own template',
  feishu_table_name: 'sender table'};

{
  const result = chooseInitialSettings({accounts: [], mailbox: ''},
    {settings: {accounts: [account]}, accounts: [account], active_mailbox: account.mailbox});
  assert.equal(result.restoredLocal, true);
  assert.equal(result.settings.accounts[0].mailbox, account.mailbox);
}

{
  const server = {accounts: [{...account, feishu_table_id: 'tbl123'}], mailbox: account.mailbox};
  const result = chooseInitialSettings(server, {settings: {accounts: [account]}});
  assert.equal(result.restoredLocal, false);
  assert.equal(result.settings.accounts[0].feishu_table_id, 'tbl123');
}

console.log('offline-saved account survives when a newly started queue is blank');
