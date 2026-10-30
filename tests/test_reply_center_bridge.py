import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from reply_center_bridge import process_event


class ReplyCenterTests(unittest.TestCase):
    def setUp(self):
        self.config = {"token": "local_token", "accounts": [{"mailbox": "sender@gmail.com",
            "feishu_table_id": "tblOwn"}]}
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

    def test_colleague_own_table_is_sent_to_center_queue(self):
        event = {**self.event, "mailbox": "colleague@gmail.com", "table_id": "tblColleague"}
        captured = []
        status, _ = process_event(event, self.config, post_local=lambda scan, _: (
            captured.append(scan) or {"replies_found": True,
                "reply_writeback": [{"ok": True}],
                "notify": [{"ok": True, "status": "sent", "message_id": "om_123"}]}))
        self.assertEqual(status, 200)
        self.assertEqual(captured[0]["feishu_table_id"], "tblColleague")

    def test_old_reply_is_skipped_without_group_send(self):
        config = {**self.config, "reply_notify": {
            "start_after": "2026-10-30T12:00:00+08:00"}}
        status, result = process_event(self.event, config,
            post_local=lambda *_: self.fail('historical mail reached center queue'))
        self.assertEqual(status, 200)
        self.assertEqual(result['status'], 'historical_skipped')


if __name__ == "__main__":
    unittest.main()
