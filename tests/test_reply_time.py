import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from local_queue import parse_gmail_reply_time


class ReplyTimeTests(unittest.TestCase):
    def test_chinese_gmail_date_becomes_actual_reply_time_in_shanghai(self):
        self.assertEqual(parse_gmail_reply_time("2026年10月30日周五 01:58"),
                         "2026-10-30T01:58:00+08:00")

    def test_unrecognized_date_does_not_become_scan_time(self):
        self.assertIsNone(parse_gmail_reply_time("unknown"))


if __name__ == "__main__":
    unittest.main()
