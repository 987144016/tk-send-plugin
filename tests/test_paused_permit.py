import unittest
from unittest.mock import patch

from python import local_queue as queue


class PausedPermitTests(unittest.TestCase):
    def test_paused_queue_refuses_send_permission_for_an_already_claimed_job(self):
        handler = queue.Handler.__new__(queue.Handler)
        handler.path = '/api/browser-mail/worker/permit'
        handler.headers = {'Authorization': 'Bearer test-token'}
        handler._read = lambda: {'prepared_snapshot': {'template_name': 'saved template'}}
        replies = []
        handler._json = lambda code, data: replies.append((code, data))
        config = {'token': 'test-token', 'template_name': 'saved template'}
        entry = {'token': 'test-token', 'template_name': 'saved template'}
        with patch.object(queue, 'cfg', return_value=config), \
                patch.object(queue, 'active_mailbox_entry', return_value=('sender@gmail.com', entry)), \
                patch.object(queue, 'state', return_value={'enabled': False, 'current': {'job_id': 'old'}}):
            handler.do_POST()
        self.assertEqual(replies[0][0], 409)
        self.assertFalse(replies[0][1].get('permit', False))


if __name__ == '__main__':
    unittest.main()
