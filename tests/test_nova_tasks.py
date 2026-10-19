from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nova_runtime import TaskError, Workspace
from nova_tasks import TaskStore, Task
import test_nova_runtime as runtime_tests


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'tasks.sqlite3'
        self.store = TaskStore(self.path)
        self.addCleanup(lambda: self.store.close())

    def test_message_deduplicates_across_restarts(self):
        task = self.store.create(42, 100, 'request')
        self.store.close()
        self.store = TaskStore(self.path)
        again = self.store.create(42, 100, 'retry')
        self.assertEqual(task.id, again.id)
        self.assertEqual(len(self.store.recent()), 1)

    def test_recovery_preserves_stage_and_metadata(self):
        task = self.store.create(42, 100, 'request')
        task.mark('committed', commit='abc', files=['game_engine/index.html'])
        self.store.recover_startup()
        row = self.store.get(task.id)
        self.assertEqual((row['status'], row['phase']), ('interrupted', 'committed'))
        self.assertEqual(row['details']['commit'], 'abc')

    def test_reply_failure_does_not_undo_success(self):
        task = self.store.create(42, 100, 'request')
        task.mark('pushed', status='completed')
        task.fail('Telegram unavailable')
        self.assertEqual(self.store.get(task.id)['status'], 'completed')
        self.assertEqual(self.store.pending(), [])

    def test_summary_masks_known_credentials(self):
        task = self.store.create(42, 100, 'key ' + 'ghp_' + 'x' * 36)
        self.assertNotIn('ghp_', self.store.summary(task.id))
        self.assertIn('[REDACTED]', self.store.summary(task.id))

    def test_interrupted_request_without_files_is_not_replayed(self):
        task = self.store.create(42, 100, 'request')
        self.store.recover_startup()
        result = Workspace(Path(self.temp.name)).resume(task)
        self.assertIn('failed', result)
        self.assertEqual(self.store.pending(), [])


class ResumeTests(unittest.TestCase):
    cmd = runtime_tests.GitTests.cmd

    def setUp(self):
        runtime_tests.GitTests.setUp(self)
        self.store = TaskStore(self.root / '.nova/tasks.sqlite3')
        self.addCleanup(self.store.close)
        self.task = self.store.create(42, 100, 'update index')

    def crash_before_checkpoint(self, stage):
        original = self.task.mark
        def mark(phase, *args, **kwargs):
            if phase == stage:
                raise SystemExit('simulated process termination')
            return original(phase, *args, **kwargs)
        return patch.object(self.task, 'mark', side_effect=mark)

    def test_crash_after_write_resumes_without_rewriting(self):
        with self.crash_before_checkpoint('saved'), self.assertRaises(SystemExit):
            self.workspace.save('game_engine/index.html', 'new', task=self.task)
        self.store.recover_startup()
        with patch('nova_runtime.atomic_write', side_effect=AssertionError('must not rewrite')):
            self.workspace.resume(self.task)
        self.assertEqual(self.store.get(self.task.id)['status'], 'completed')
        self.assertEqual(self.cmd('rev-list', '--count', 'HEAD'), '2')
        self.assertEqual(self.cmd('show', 'nova/work:game_engine/index.html', cwd=self.remote), 'new')

    def test_crash_after_commit_finds_trailer_without_duplicate_commit(self):
        with self.crash_before_checkpoint('committed'), self.assertRaises(SystemExit):
            self.workspace.save('game_engine/index.html', 'new', task=self.task)
        sha = self.cmd('rev-parse', 'HEAD')
        self.store.recover_startup()
        self.workspace.resume(self.task)
        self.assertEqual(self.cmd('rev-parse', 'HEAD'), sha)
        self.assertEqual(self.store.get(self.task.id)['details']['commit'], sha)

    def test_crash_after_push_is_idempotent(self):
        with self.crash_before_checkpoint('pushed'), self.assertRaises(SystemExit):
            self.workspace.save('game_engine/index.html', 'new', task=self.task)
        sha = self.cmd('rev-parse', 'HEAD')
        self.store.recover_startup()
        self.workspace.resume(self.task)
        self.workspace.resume(self.task)
        self.assertEqual(self.cmd('rev-parse', 'HEAD'), sha)
        self.assertEqual(self.store.get(self.task.id)['phase'], 'pushed')

    def test_changed_file_after_crash_is_preserved(self):
        with self.crash_before_checkpoint('saved'), self.assertRaises(SystemExit):
            self.workspace.save('game_engine/index.html', 'new', task=self.task)
        target = self.root / 'game_engine/index.html'
        target.write_text('human correction')
        with self.assertRaises(TaskError):
            self.workspace.resume(self.task)
        self.assertEqual(target.read_text(), 'human correction')
        self.assertEqual(self.cmd('rev-parse', 'HEAD'), self.base)

    def test_push_failure_records_pending_and_recovers(self):
        self.cmd('remote', 'set-url', 'origin', str(self.remote) + '-missing')
        with self.assertRaises(TaskError) as error:
            self.workspace.save('game_engine/index.html', 'new', task=self.task)
        self.task.fail(str(error.exception))
        self.assertEqual(self.store.get(self.task.id)['status'], 'awaiting_sync')
        self.cmd('remote', 'set-url', 'origin', str(self.remote))
        self.workspace.resume(self.task)
        self.assertEqual(self.store.get(self.task.id)['status'], 'completed')

    def test_crash_before_write_does_not_invent_changes(self):
        with patch('nova_runtime.atomic_write', side_effect=SystemExit), self.assertRaises(SystemExit):
            self.workspace.save('game_engine/index.html', 'new', task=self.task)
        self.workspace.resume(self.task)
        self.assertEqual(self.store.get(self.task.id)['status'], 'failed')
        self.assertEqual((self.root / 'game_engine/index.html').read_text(), 'old')

    def test_other_staged_file_prevents_recovery_commit(self):
        with self.crash_before_checkpoint('saved'), self.assertRaises(SystemExit):
            self.workspace.save('game_engine/index.html', 'new', task=self.task)
        (self.root / 'RESUME.md').write_text('another task')
        self.cmd('add', 'RESUME.md')
        with self.assertRaises(TaskError):
            self.workspace.resume(self.task)
        self.assertEqual(self.cmd('diff', '--cached', '--name-only'), 'RESUME.md')


if __name__ == '__main__':
    unittest.main()
