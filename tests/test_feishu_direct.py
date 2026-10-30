import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from python import feishu_direct as direct
from python import feishu_cli
from python import local_queue


class DirectFeishuTest(unittest.TestCase):
    def test_table_name_resolves_exact_id_across_pages(self):
        pages = [
            {"items": [{"name": "other", "table_id": "tblOther"}],
             "has_more": True, "page_token": "next"},
            {"items": [{"name": "sender@gmail.com", "table_id": "tblWanted"}],
             "has_more": False},
        ]
        with patch.object(direct, "_api", side_effect=pages):
            self.assertEqual(direct.resolve_table_id("base", "sender@gmail.com"), "tblWanted")
        with patch.object(direct, "_api", return_value={"items": [], "has_more": False}):
            with self.assertRaisesRegex(ValueError, "匹配到 0 张表"):
                direct.resolve_table_id("base", "unknown")

    def test_saving_table_name_fills_id_in_local_queue(self):
        with tempfile.TemporaryDirectory() as td:
            queue_config = Path(td) / "one-mailbox.json"
            queue_config.write_text('{"armed":true,"accounts":[],"feishu":{}}', encoding="utf-8")
            with patch.object(local_queue, "DEFAULT_CONFIG", queue_config), patch.object(local_queue, "direct_enabled", return_value=False), patch.object(local_queue, "resolve_configured_table_id", return_value="tblWanted") as lookup:
                local_queue.apply_settings({"accounts": [{"mailbox": "sender@gmail.com",
                    "template_name": "own template", "feishu_table_name": "sender@gmail.com",
                    "feishu_table_id": "", "window_minutes": 30, "batch_size": 0}],
                    "active_mailbox": "sender@gmail.com"})
            saved = json.loads(queue_config.read_text(encoding="utf-8"))
            self.assertEqual(saved["accounts"][0]["feishu_table_id"], "tblWanted")
            lookup.assert_called_once_with(unittest.mock.ANY, "sender@gmail.com")

    def test_incomplete_private_file_keeps_queue_config_readable(self):
        with tempfile.TemporaryDirectory() as td:
            private = Path(td) / "team-feishu.json"
            private.write_text('{"app_secret":"placeholder"}', encoding="utf-8")
            queue_config = Path(td) / "one-mailbox.json"
            queue_config.write_text('{"armed":true,"feishu":{}}', encoding="utf-8")
            with patch.dict(sys.modules, {"feishu_direct": direct}), patch.object(direct, "TEAM_FILE", private), patch.object(local_queue, "DEFAULT_CONFIG", queue_config), patch.object(local_queue, "direct_enabled", return_value=True):
                result = local_queue.cfg()
            self.assertTrue(result["armed"])
            self.assertIn("app_id", result["feishu_config_error"])

    def test_config_file_is_read_from_loaded_extension_folder(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "team-feishu.json"
            p.write_text(json.dumps({"app_id": "cli_test", "app_secret": "private",
                                     "base_token": "base_test"}), encoding="utf-8")
            with patch.object(direct, "TEAM_FILE", p):
                self.assertTrue(feishu_cli.direct_enabled())
                self.assertEqual(direct.config()["base_token"], "base_test")

    def test_direct_adapter_lists_all_pages_and_writes_cli_shape(self):
        with tempfile.TemporaryDirectory() as td:
            private = Path(td) / "team-feishu.json"
            private.write_text(json.dumps({"app_id": "x", "app_secret": "y",
                                           "base_token": "b"}), encoding="utf-8")
            output = Path(td) / "records.ndjson"
            pages = [
                {"items": [{"record_id": "rec1", "fields": {"邮箱": "a@gmail.com"}}],
                 "has_more": True, "page_token": "next"},
                {"items": [{"record_id": "rec2", "fields": {"邮箱": "b@gmail.com"}}],
                 "has_more": False},
            ]
            with patch.object(direct, "TEAM_FILE", private), patch.object(direct, "_api", side_effect=pages):
                result = feishu_cli.run_lark(["lark", "base", "+record-list", "--base-token", "b",
                                                "--table-id", "tbl1", "--output", str(output)])
            self.assertEqual(result.returncode, 0)
            self.assertFalse(json.loads(result.stdout)["has_more"])
            self.assertEqual([json.loads(line)["record_id"] for line in output.read_text(encoding="utf-8").splitlines()],
                             ["rec1", "rec2"])

    def test_batch_update_converts_single_select_and_group_has_receipt(self):
        with patch.object(direct, "_api", return_value={"records": [{"record_id": "rec1"}]}) as api:
            direct.batch_update("b", "tbl1", {"update_records": {"rec1": {"发送状态": ["已发送"]}}})
            self.assertEqual(api.call_args.kwargs["data"]["records"][0]["fields"]["发送状态"], "已发送")
        with patch.object(direct, "_api", return_value={"message_id": "m1", "chat_id": "oc_123"}):
            self.assertTrue(direct.send_group_text("oc_123", "new reply", "gmail-id")["ok"])


if __name__ == "__main__":
    unittest.main()
