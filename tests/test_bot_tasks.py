import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import bot
from nova_tasks import TaskStore


class BotTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.messages = []
        self.replies = []
        outer = self

        class FakeBot:
            def __init__(self, *args, **kwargs):
                assert kwargs['threaded'] is False

            def message_handler(self, **kwargs):
                def register(handler):
                    self.handler = handler
                    return handler
                return register

            def reply_to(self, message, text):
                outer.replies.append(text)

            def send_chat_action(self, *args):
                pass

            def infinity_polling(self, **kwargs):
                for message in outer.messages:
                    self.handler(message)

        self.post = Mock(return_value=SimpleNamespace(status_code=200, json=lambda: {
            'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': 'جاهز'}]}}]}))
        self.modules = {'telebot': SimpleNamespace(TeleBot=FakeBot),
                        'requests': SimpleNamespace(post=self.post),
                        'dotenv': SimpleNamespace(load_dotenv=lambda path: None)}

    def message(self, number, text, owner=42):
        return SimpleNamespace(chat=SimpleNamespace(id=42, type='private'),
                               from_user=SimpleNamespace(id=owner), message_id=number, text=text)

    def run_bot(self):
        env = {'TELEGRAM_BOT_TOKEN': 'test', 'GEMINI_API_KEY': 'test', 'TELEGRAM_OWNER_ID': '42'}
        with patch.dict('sys.modules', self.modules), patch.dict(os.environ, env, clear=True), \
                patch.object(bot, '__file__', str(self.root / 'bot.py')):
            bot.main()

    def test_unauthorized_and_duplicate_messages_do_not_call_model(self):
        self.messages = [self.message(1, 'hi', owner=7), self.message(2, 'hi'), self.message(2, 'hi')]
        self.run_bot()
        self.assertEqual(self.post.call_count, 1)
        store = TaskStore(self.root / '.nova/tasks.sqlite3')
        self.addCleanup(store.close)
        self.assertEqual(len(store.recent()), 1)
        self.assertEqual(store.recent()[0]['phase'], 'answered')

    def test_interrupted_task_blocks_new_request_until_reconciled(self):
        store = TaskStore(self.root / '.nova/tasks.sqlite3')
        task = store.create(42, 1, 'previous request')
        store.close()
        self.messages = [self.message(2, 'new request'), self.message(3, '/tasks'),
                         self.message(4, '/resume'), self.message(5, 'new request')]
        self.run_bot()
        self.assertEqual(self.post.call_count, 1)
        store = TaskStore(self.root / '.nova/tasks.sqlite3')
        self.addCleanup(store.close)
        self.assertEqual(store.get(task.id)['status'], 'failed')
        self.assertEqual(store.recent()[0]['status'], 'completed')

    def test_provider_failure_has_task_record_without_raw_error_body(self):
        self.post.return_value = SimpleNamespace(status_code=429, text='PRIVATE ERROR')
        self.messages = [self.message(1, 'hello')]
        self.run_bot()
        store = TaskStore(self.root / '.nova/tasks.sqlite3')
        self.addCleanup(store.close)
        self.assertEqual(store.recent()[0]['status'], 'failed')
        self.assertNotIn('PRIVATE ERROR', '\n'.join(self.replies))
        self.assertIn('429', '\n'.join(self.replies))
