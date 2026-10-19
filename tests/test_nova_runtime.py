import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from nova_runtime import TaskError, Workspace, authorized, parse_save, safe_path


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_owner_and_private_chat_required(self):
        message = SimpleNamespace(chat=SimpleNamespace(type='private'), from_user=SimpleNamespace(id=42))
        self.assertTrue(authorized(message, 42))
        self.assertFalse(authorized(message, 7))
        message.chat.type = 'group'
        self.assertFalse(authorized(message, 42))

    def test_path_escape_and_sensitive_files_rejected(self):
        for path in ('../secret', '/tmp/file.js', 'game_engine/../../x.js',
                     'game_engine/.env', '.env', 'bot.py', 'game_engine/x;echo.js',
                     'game_engine/x$(echo).js', 'game_engine/x`echo`.js',
                     'game_engine/evil.py', '.git/config', 'game_engine/.git/config'):
            with self.subTest(path=path), self.assertRaises(TaskError):
                safe_path(self.root, path)

    def test_symlink_rejected(self):
        (self.root / 'game_engine').symlink_to('/tmp', target_is_directory=True)
        with self.assertRaises(TaskError):
            safe_path(self.root, 'game_engine/a.js')

    def test_single_file_response_and_non_code_reply(self):
        self.assertIsNone(parse_save('مرحبا'))
        self.assertEqual(parse_save('[SAVE: game_engine/a.js]\n```js\nlet x = 1;\n```'),
                         ('game_engine/a.js', 'let x = 1;\n'))

    def test_ambiguous_or_incomplete_responses_rejected(self):
        for reply in ('[SAVE: game_engine/a.js] no fence',
                      '[SAVE: game_engine/a.js]\n```js\nx\n```\n```js\ny\n```',
                      '[SAVE: game_engine/a.js][SAVE: game_engine/b.js]\n```js\nx\n```',
                      '[SAVE: game_engine/a.json]\n```json\n{bad}\n```'):
            with self.subTest(reply=reply), self.assertRaises(TaskError):
                parse_save(reply)

    def test_credentials_rejected(self):
        value = 'ghp_' + 'a' * 36
        with self.assertRaises(TaskError):
            parse_save('[SAVE: game_engine/a.js]\n```js\n' + value + '\n```')


class GitTests(unittest.TestCase):
    def cmd(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd or self.root,
                              check=True, capture_output=True, text=True).stdout.strip()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'work'
        self.root.mkdir()
        self.remote = Path(self.temp.name) / 'remote.git'
        self.cmd('init', '--bare', str(self.remote))
        self.cmd('init', '-b', 'main')
        self.cmd('config', 'user.email', 'test@example.invalid')
        self.cmd('config', 'user.name', 'NOVA Test')
        (self.root / 'game_engine').mkdir()
        (self.root / 'game_engine/index.html').write_text('old')
        (self.root / 'RESUME.md').write_text('resume')
        self.cmd('add', '.')
        self.cmd('commit', '-m', 'initial')
        self.cmd('remote', 'add', 'origin', str(self.remote))
        self.cmd('push', 'origin', 'main')
        self.base = self.cmd('rev-parse', 'HEAD')
        self.workspace = Workspace(self.root)

    def test_commit_push_only_intended_file_on_work_branch(self):
        (self.root / 'unrelated.txt').write_text('private scratch')
        result = self.workspace.save('game_engine/index.html', 'new')
        self.assertIn('Commit:', result)
        self.assertEqual(self.cmd('branch', '--show-current'), 'nova/work')
        self.assertEqual(self.cmd('show', '--pretty=', '--name-only', 'HEAD'), 'game_engine/index.html')
        self.assertEqual(self.cmd('rev-parse', 'main'), self.base)
        self.assertEqual(self.cmd('rev-parse', 'nova/work', cwd=self.remote), self.cmd('rev-parse', 'HEAD'))
        self.assertFalse((self.root / 'game_engine/.nova-').exists())

    def test_push_failure_preserves_commit_and_sync_recovers(self):
        self.cmd('remote', 'set-url', 'origin', str(self.remote) + '-missing')
        with self.assertRaisesRegex(TaskError, '/sync'):
            self.workspace.save('game_engine/index.html', 'new')
        self.assertNotEqual(self.cmd('rev-parse', 'HEAD'), self.base)
        self.cmd('remote', 'set-url', 'origin', str(self.remote))
        self.assertIn('Commit:', self.workspace.sync())

    def test_dirty_target_never_overwritten(self):
        target = self.root / 'game_engine/index.html'
        target.write_text('human changes')
        with self.assertRaises(TaskError):
            self.workspace.save('game_engine/index.html', 'model changes')
        self.assertEqual(target.read_text(), 'human changes')

    def test_unrelated_staged_file_blocks_commit(self):
        (self.root / 'RESUME.md').write_text('another task')
        self.cmd('add', 'RESUME.md')
        with self.assertRaises(TaskError):
            self.workspace.save('game_engine/index.html', 'new')
        self.assertEqual((self.root / 'game_engine/index.html').read_text(), 'old')

    def test_second_task_works_on_existing_work_branch(self):
        self.workspace.save('game_engine/index.html', 'first')
        self.workspace.save('game_engine/index.html', 'second')
        self.assertEqual(self.cmd('show', 'nova/work:game_engine/index.html', cwd=self.remote), 'second')


if __name__ == '__main__':
    unittest.main()
