import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from python import feishu_direct as direct
from python import feishu_cli


class DirectFeishuTest(unittest.TestCase):
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
