import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from python import local_queue as queue


class FreshResumeTests(unittest.TestCase):
    def test_resuming_after_pause_drops_old_unsent_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = root / 'jobs'
            jobs.mkdir()
            (jobs / 'pending-old.json').write_text(json.dumps({
                'job_id': 'old', 'recipients': ['old@example.com'],
                'gmail_template_name': 'old template', 'people_count': 1,
            }), encoding='utf-8')
            state_file = root / 'queue-state.json'
            state_file.write_text(json.dumps({'enabled': False, 'current': None}), encoding='utf-8')
            with patch.object(queue, 'JOBS', jobs), patch.object(queue, 'STATE_FILE', state_file):
                result = queue.reset_unsent_for_fresh_start()
                self.assertEqual(result['cancelled'], 1)
                self.assertEqual(queue.list_pending(), [])
                self.assertEqual(queue.queued_emails(), set())
                self.assertTrue((jobs / 'cancelled-old.json').exists())

    def test_resuming_does_not_discard_a_send_in_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = root / 'jobs'
            jobs.mkdir()
            (jobs / 'pending-old.json').write_text('{}', encoding='utf-8')
            state_file = root / 'queue-state.json'
            state_file.write_text(json.dumps({'enabled': False, 'current': {'job_id': 'maybe-sent'}}), encoding='utf-8')
            with patch.object(queue, 'JOBS', jobs), patch.object(queue, 'STATE_FILE', state_file):
                with self.assertRaisesRegex(ValueError, 'unfinished'):
                    queue.reset_unsent_for_fresh_start()
                self.assertTrue((jobs / 'pending-old.json').exists())


if __name__ == '__main__':
    unittest.main()
