import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HOST_PATH = Path(__file__).resolve().parents[1] / 'native-host' / 'tk_send_queue_host.py'
spec = importlib.util.spec_from_file_location('tk_send_queue_host', HOST_PATH)
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class NativeRestartTests(unittest.TestCase):
    def test_does_not_interrupt_active_gmail_send(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'data').mkdir()
            (root / 'data' / 'queue-state.json').write_text(
                json.dumps({'current': {'job_id': 'job1'}}), encoding='utf-8')
            with patch.object(host, 'ROOT', root), patch.object(host, 'stop_queue') as stop:
                result = host.restart_queue()
            self.assertEqual(result['error'], 'send_in_progress')
            stop.assert_not_called()

    def test_idle_queue_restarts_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'data').mkdir()
            (root / 'data' / 'queue-state.json').write_text(
                json.dumps({'current': None}), encoding='utf-8')
            with patch.object(host, 'ROOT', root), \
                 patch.object(host, 'stop_queue', return_value={'ok': True}) as stop, \
                 patch.object(host, 'health', return_value=None), \
                 patch.object(host, 'start_queue', return_value={'ok': True}) as start:
                self.assertTrue(host.restart_queue()['ok'])
            stop.assert_called_once()
            start.assert_called_once()


if __name__ == '__main__':
    unittest.main()
