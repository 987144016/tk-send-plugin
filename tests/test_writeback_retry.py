import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
import local_queue


class WritebackRetryTests(unittest.TestCase):
    def test_sent_mail_waits_for_feishu_writeback_without_resending(self):
        with tempfile.TemporaryDirectory() as folder:
            pending = Path(folder)
            item = pending / 'job1.json'
            item.write_text(json.dumps({'job_id': 'job1', 'mailbox': 'sender@gmail.com',
                'feishu_table_id': 'tblTeam', 'record_ids': ['recA'],
                'recipients': ['creator@gmail.com'], 'sent_at': '2026-10-30 15:48:06'}),
                encoding='utf-8')
            with patch.object(local_queue, 'PENDING_WRITEBACKS', pending), \
                 patch.object(local_queue, 'cfg', return_value={'feishu': {'base_token': 'base'}}), \
                 patch.object(local_queue, 'mark_feishu_sent', side_effect=[
                     {'ok': False}, {'ok': True}]) as write:
                self.assertIn('creator@gmail.com', local_queue.queued_emails())
                local_queue.retry_pending_writebacks()
                self.assertTrue(item.exists())
                local_queue.retry_pending_writebacks()
                self.assertFalse(item.exists())
                self.assertEqual(write.call_args.kwargs['sent_at'], '2026-10-30 15:48:06')


if __name__ == '__main__':
    unittest.main()
