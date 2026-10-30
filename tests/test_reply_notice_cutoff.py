import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from local_queue import is_historical_reply_notice


class ReplyNoticeCutoffTests(unittest.TestCase):
    def test_existing_inbox_reply_is_skipped_but_future_reply_is_not(self):
        config = {"reply_notify": {"start_after": "2026-10-30T11:20:03+08:00"}}
        self.assertTrue(is_historical_reply_notice(config, "2026-10-28T21:30:00+08:00"))
        self.assertTrue(is_historical_reply_notice(config, "2026-10-30T11:20:03+08:00"))
        self.assertFalse(is_historical_reply_notice(config, "2026-10-30T11:20:04+08:00"))


if __name__ == "__main__":
    unittest.main()
