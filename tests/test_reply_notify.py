import tempfile
import unittest
import json
import hashlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))


class ReplyNotifyTests(unittest.TestCase):
    def test_a_matched_reply_is_announced_once_to_the_configured_group(self):
        from reply_notify import notify_reply

        sent = []
        config = {"reply_notify": {
            "enabled": True, "chat_id": "oc_team", "profile": "teammate",
        }}
        reply = {
            "from": "janaeelliott1003@gmail.com",
            "message_id": "1a188d0a97c76728",
            "thread_url": "https://mail.google.com/mail/u/3/#all/1a1863ec24fa5b7d",
            "snippet": "Hello, my rate is $250 for a video with the ad code.",
            "body": "Hello, my rate is $250 for a video with the ad code.\nPlease let me know if you'd like to proceed.",
            "body_verified": True,
            "at": "2026年10月30日周五 01:58",
        }
        contact = {"creator_id": "ebonietts", "feishu_record_id": "rec_janae"}

        def transport(settings, key, body):
            sent.append((settings, key, body))
            return {"ok": True, "data": {"chat_id": "oc_team", "message_id": "om_accepted"}}

        with tempfile.TemporaryDirectory() as tmp:
            first = notify_reply(config, "wuxiaoyu28le@gmail.com", reply, contact,
                                 ledger=Path(tmp), transport=transport)
            second = notify_reply(config, "wuxiaoyu28le@gmail.com", reply, contact,
                                  ledger=Path(tmp), transport=transport)
        self.assertEqual(first["status"], "sent")
        self.assertEqual(second["status"], "duplicate")
        self.assertTrue(first["sent_at"])
        self.assertEqual(second["sent_at"], first["sent_at"])
        self.assertEqual(len(sent), 1)
        self.assertIn("ebonietts", sent[0][2])
        self.assertIn("Hello, my rate", sent[0][2])
        self.assertIn("Please let me know", sent[0][2])

    def test_historical_baseline_never_sends_a_group_message(self):
        from reply_notify import notify_reply

        config = {"reply_notify": {"enabled": True, "chat_id": "oc_team", "profile": "center"}}
        mailbox = "sender@gmail.com"
        reply = {"from": "creator@gmail.com", "message_id": "1a182b44a7b872ea"}
        identity = "|".join(("oc_team", mailbox, reply["message_id"]))
        key = "send-plugin-reply-" + hashlib.sha256(identity.encode()).hexdigest()[:32]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / (key + ".json")
            path.write_text(json.dumps({"status": "historical_skipped", "gmail_message_id": reply["message_id"]}), encoding="utf-8")
            def must_not_send(*_args):
                self.fail("historical mail must not be broadcast")
            result = notify_reply(config, mailbox, reply, {}, ledger=Path(tmp), transport=must_not_send)
        self.assertEqual(result["status"], "historical_skipped")

    def test_missing_full_body_waits_and_later_verified_body_sends_once(self):
        from reply_notify import notify_reply

        calls = []
        config = {"reply_notify": {"enabled": True, "chat_id": "oc_team", "profile": "center"}}
        reply = {"from": "creator@gmail.com", "message_id": "1a182b44a7b872ea",
                 "snippet": "short preview", "body_verified": False}
        def transport(_settings, _key, _body):
            calls.append(1)
            return {"ok": True, "data": {"chat_id": "oc_team", "message_id": "om_accepted"}}
        with tempfile.TemporaryDirectory() as tmp:
            pending = notify_reply(config, "sender@gmail.com", reply, {},
                                   ledger=Path(tmp), transport=transport)
            self.assertEqual(list(Path(tmp).glob("*.json")), [])
            reply.update(body_verified=True, body="Complete reply text")
            sent = notify_reply(config, "sender@gmail.com", reply, {},
                                ledger=Path(tmp), transport=transport)
        self.assertEqual(pending["status"], "body_pending")
        self.assertEqual(sent["status"], "sent")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
