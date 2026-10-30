import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import local_queue


class OptionsPreservationTests(unittest.TestCase):
    def test_saving_scan_interval_does_not_clear_center_group_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "one-mailbox.json"
            config_path.write_text(json.dumps({
                "armed": False,
                "reply_notify": {"enabled": True, "chat_id": "oc_team", "profile": "center"},
                "reply_history_table_ids": ["tblHistory123"],
                "feishu": {"base_token": "base", "table_id": "tblCurrent123"},
                "accounts": [{"mailbox": "sender@gmail.com", "template_name": "own template",
                              "feishu_table_name": "sender", "feishu_table_id": "tblCurrent123",
                              "pace": {"window_minutes": 30, "max_messages_per_window": 0}}],
                "active_mailbox": "sender@gmail.com",
            }), encoding="utf-8")
            with patch.object(local_queue, "DEFAULT_CONFIG", config_path):
                local_queue.apply_settings({
                    "accounts": [{"mailbox": "sender@gmail.com", "template_name": "own template",
                                  "feishu_table_name": "sender", "feishu_table_id": "tblCurrent123",
                                  "window_minutes": 30, "batch_size": 0}],
                    "active_mailbox": "sender@gmail.com",
                    "reply_scan": {"enabled": True, "interval_minutes": 5},
                })
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["reply_notify"],
                             {"enabled": True, "chat_id": "oc_team", "profile": "center"})
            self.assertEqual(saved["reply_history_table_ids"], ["tblHistory123"])
            visible = local_queue.settings_view(saved)
            self.assertNotIn("reply_notify", visible)
            self.assertNotIn("reply_history_table_ids", visible)


if __name__ == "__main__":
    unittest.main()
