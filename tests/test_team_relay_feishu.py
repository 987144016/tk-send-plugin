import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import feishu_cli


class TeamRelayFeishuTests(unittest.TestCase):
    def test_colleague_list_receives_rows_without_local_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "team-relay.json"
            config.write_text(json.dumps({"relay_url": "https://relay.example",
                "client_key": "private"}), encoding="utf-8")
            output = root / "rows.ndjson"

            class Response:
                def __enter__(self):
                    return self
                def __exit__(self, *_):
                    pass

            with patch.object(feishu_cli, "direct_enabled", return_value=False), patch.object(feishu_cli, "TEAM_RELAY", config), patch.object(
                feishu_cli.urllib.request, "urlopen", return_value=Response()
            ), patch.object(feishu_cli.json, "load", return_value={"ok": True,
                "returncode": 0, "stdout": '{"has_more":false}',
                "output": '{"record_id":"recA","邮箱":"x@gmail.com"}\n'}):
                result = feishu_cli.run_lark(["missing-lark.exe", "base", "+record-list",
                    "--offset", "0", "--output", str(output)], timeout=5)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))['record_id'], 'recA')


if __name__ == '__main__':
    unittest.main()
