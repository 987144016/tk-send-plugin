import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from reply_center_bridge import process_event


class ReplyCenterTests(unittest.TestCase):
    def setUp(self):
        self.config = {"token": "local_token", "accounts": [{"mailbox": "sender@gmail.com"}]}
        self.event = {"mailbox": "sender@gmail.com", "reply": {
            "from": "creator@gmail.com", "message_id": "1a188d0a97c76728",
            "at": "2026年10月30日周五 01:58", "body": "My rate is $250.",
            "body_verified": True,
        }}

    def test_ack_only_after_feishu_group_receipt(self):
        captured = []
        def accepted(scan, _config):
            captured.append(scan)
            return {"replies_found": True, "reply_writeback": [{"ok": True}],
                    "notify": [{"ok": True, "status": "sent", "message_id": "om_123"}]}
        status, result = process_event(self.event, self.config, post_local=accepted)
        self.assertEqual((status, result["status"]), (200, "sent"))
        self.assertEqual(captured[0]["reason"], "remote_reply")
        self.assertEqual(captured[0]["replies"][0]["body"], "My rate is $250.")

    def test_center_offline_or_unconfirmed_notice_stays_pending(self):
        status, result = process_event(self.event, self.config,
            post_local=lambda *_: {"replies_found": True,
                "reply_writeback": [{"ok": True}], "notify": []})
        self.assertEqual(status, 503)
        self.assertEqual(result["error"], "group_notice_unconfirmed")


if __name__ == "__main__":
    unittest.main()
