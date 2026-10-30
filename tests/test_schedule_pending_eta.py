import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from python import local_queue as queue


class SchedulePendingEtaTests(unittest.TestCase):
    def test_active_draft_reports_send_is_unconfirmed(self):
        eta, _, human = queue._armed_eta_fields(
            remaining=22, next_count=15, allowed_ts=100, now=100,
            pending_n=1, mailbox='sender@gmail.com', is_active=True,
            feishu_err=None, schedule_pending=1, active_n=1, window_minutes=30)
        self.assertIsNone(eta)
        self.assertIn('尚未确认发出', human)
        self.assertIn('隔 30 分钟', human)

    def test_queued_job_retains_future_earliest_time(self):
        eta, _, human = queue._armed_eta_fields(
            remaining=22, next_count=15, allowed_ts=400, now=100,
            pending_n=1, mailbox='sender@gmail.com', is_active=True,
            feishu_err=None, schedule_pending=1)
        self.assertEqual(eta, 400)
        self.assertIn('约 5 分钟', human)

    def test_active_and_waiting_jobs_are_distinguished(self):
        with tempfile.TemporaryDirectory() as td:
            jobs = Path(td)
            (jobs / 'active-one.json').write_text(json.dumps({
                'sender': 'sender@gmail.com', 'enqueue_reason': 'schedule'}), encoding='utf-8')
            (jobs / 'pending-two.json').write_text(json.dumps({
                'sender': 'sender@gmail.com', 'enqueue_reason': 'schedule'}), encoding='utf-8')
            with patch.object(queue, 'JOBS', jobs):
                counts = queue.pending_job_breakdown('sender@gmail.com')
        self.assertEqual(counts['schedule_pending'], 2)
        self.assertEqual(counts['active_schedule'], 1)


if __name__ == '__main__':
    unittest.main()
