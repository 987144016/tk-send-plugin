import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import local_queue


class HeldJobsTests(unittest.TestCase):
    def test_held_recipients_are_not_selected_for_a_new_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Path(tmp)
            (jobs / "held-existing.json").write_text(json.dumps({
                "recipients": ["first@gmail.com", "second@gmail.com"],
            }), encoding="utf-8")
            with patch.object(local_queue, "JOBS", jobs):
                self.assertEqual(local_queue.queued_emails(),
                                 {"first@gmail.com", "second@gmail.com"})


if __name__ == "__main__":
    unittest.main()
