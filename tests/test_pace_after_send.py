import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
import local_queue


class PaceAfterSendTests(unittest.TestCase):
    def setUp(self):
        self.config = {'pace': {'window_minutes': 10,
            'max_messages_per_window': 2, 'min_gap_seconds': 90,
            'daily_limit': 500}}
        self.mailbox = 'sender@gmail.com'

    def test_full_bcc_batch_waits_ten_minutes_from_completion(self):
        event = {'mailbox': self.mailbox, 'status': 'sent', 'ts': 1000, 'people': 2}
        with patch.object(local_queue, 'read_pace_events', return_value=[event]), \
             patch.object(local_queue.time, 'time', return_value=1100):
            self.assertEqual(local_queue.next_pace_allowed_ts(self.config, self.mailbox)[0], 1600)
            self.assertFalse(local_queue.pace_ok(self.config, self.mailbox)[0])
        with patch.object(local_queue, 'read_pace_events', return_value=[event]), \
             patch.object(local_queue.time, 'time', return_value=1600):
            self.assertTrue(local_queue.pace_ok(self.config, self.mailbox)[0])

    def test_partial_sends_start_cooldown_after_last_person(self):
        events = [
            {'mailbox': self.mailbox, 'status': 'sent', 'ts': 1000, 'people': 1},
            {'mailbox': self.mailbox, 'status': 'sent', 'ts': 1060, 'people': 1},
        ]
        with patch.object(local_queue, 'read_pace_events', return_value=events), \
             patch.object(local_queue.time, 'time', return_value=1160):
            self.assertEqual(local_queue.next_pace_allowed_ts(self.config, self.mailbox)[0], 1660)
            self.assertFalse(local_queue.pace_ok(self.config, self.mailbox)[0])


if __name__ == '__main__':
    unittest.main()
