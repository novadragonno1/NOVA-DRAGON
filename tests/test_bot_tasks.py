import json
import os
from pathlib import Path
import tempfile
import subprocess
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

    def skill_file(self):
        path = self.root / 'ai_tools/agent-skills/skills/review/SKILL.md'
        path.parent.mkdir(parents=True)
        path.write_text('UNIQUE REVIEW GUIDANCE')
        return path

    def test_skill_selection_persists_and_reaches_model_with_task_evidence(self):
        self.skill_file()
        self.messages = [self.message(1, '/skill review')]
        self.run_bot()
        self.post.assert_not_called()
        self.messages = [self.message(2, 'review current files')]
        self.run_bot()
        system = self.post.call_args.kwargs['json']['system_instruction']['parts'][0]['text']
        self.assertIn('UNIQUE REVIEW GUIDANCE', system)
        store = TaskStore(self.root / '.nova/tasks.sqlite3')
        self.addCleanup(store.close)
        evidence = store.recent()[0]['details']['skills']
        self.assertEqual(evidence[0]['name'], 'review')
        self.assertEqual(len(evidence[0]['sha256']), 64)

    def test_missing_active_skill_blocks_model_and_records_failure(self):
        path = self.skill_file()
        self.messages = [self.message(1, '/skill review')]
        self.run_bot()
        path.unlink()
        self.messages = [self.message(2, 'review')]
        self.run_bot()
        self.post.assert_not_called()
        store = TaskStore(self.root / '.nova/tasks.sqlite3')
        self.addCleanup(store.close)
        self.assertEqual(store.recent()[0]['status'], 'failed')

    def test_reset_clears_skills_and_unauthorized_selection_is_ignored(self):
        self.skill_file()
        self.messages = [self.message(1, '/skill review', owner=7), self.message(2, 'hello'),
                         self.message(3, '/skill review'), self.message(4, '/reset'), self.message(5, 'hello')]
        self.run_bot()
        self.assertEqual(self.post.call_count, 2)
        for call in self.post.call_args_list:
            self.assertNotIn('UNIQUE REVIEW GUIDANCE', str(call.kwargs['json']))

    def test_tools_and_catalog_do_not_call_model(self):
        self.skill_file()
        self.messages = [self.message(1, '/tools'), self.message(2, '/skills')]
        self.run_bot()
        self.post.assert_not_called()
        self.assertIn('review', '\n'.join(self.replies))
        self.assertIn('غير مربوطة', '\n'.join(self.replies))

    def test_read_then_multi_file_response_commits_one_batch(self):
        def git(*args):
            return subprocess.run(['git', *args], cwd=self.root, check=True,
                                  text=True, capture_output=True).stdout.strip()
        remote = Path(self.temp.name) / 'remote.git'
        git('init', '--bare', str(remote))
        git('init', '-b', 'main')
        git('config', 'user.name', 'NOVA Test')
        git('config', 'user.email', 'test@example.invalid')
        (self.root / 'game_engine').mkdir()
        (self.root / 'game_engine/index.html').write_text('<html></html>')
        git('add', 'game_engine/index.html')
        git('commit', '-m', 'initial')
        git('remote', 'add', 'origin', str(remote))
        payload = {'files': [
            {'path': 'game_engine/index.html', 'content': '<script src="app.js"></script>'},
            {'path': 'game_engine/app.js', 'content': 'const answer = 42;'}]}
        answer = '[FILES]\n```json\n' + json.dumps(payload) + '\n```'
        self.post.return_value = SimpleNamespace(status_code=200, json=lambda: {
            'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': answer}]}}]})
        self.messages = [self.message(1, '/read game_engine/app.js'), self.message(2, 'apply both files')]
        # Preserve PATH so the real static checker can locate Node.
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'test', 'GEMINI_API_KEY': 'test',
                                    'TELEGRAM_OWNER_ID': '42'}), \
                patch.dict('sys.modules', self.modules), \
                patch.object(bot, '__file__', str(self.root / 'bot.py')):
            bot.main()
        self.assertEqual(git('rev-list', '--count', 'HEAD'), '2')
        self.assertEqual((self.root / 'game_engine/app.js').read_text(), 'const answer = 42;')
        store = TaskStore(self.root / '.nova/tasks.sqlite3')
        self.addCleanup(store.close)
        self.assertEqual(store.recent()[0]['status'], 'completed')
