from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import test_nova_runtime as runtime_tests
from nova_runtime import TaskError, Workspace, atomic_write
from nova_tasks import TaskStore, Task
from nova_portability import export_state, read_archive, restore_state, state_lock


class TransferTests(unittest.TestCase):
    cmd = runtime_tests.GitTests.cmd

    def setUp(self):
        process_scan = patch('nova_portability.bot_pids', return_value=[])
        process_scan.start()
        self.addCleanup(process_scan.stop)
        runtime_tests.GitTests.setUp(self)
        state = self.root / '.nova'
        state.mkdir()
        self.history = {'history': [{'role': 'user', 'parts': [{'text': 'أكمل العمل'}]}],
                        'last_message_id': 10, 'selected': ['game_engine/app.js'],
                        'skills': ['planning-and-task-breakdown']}
        (state / 'history.json').write_text(json.dumps(self.history))
        self.store = TaskStore(state / 'tasks.sqlite3')
        self.addCleanup(self.store.close)
        self.task = self.store.create(42, 10, 'test task')
        self.task.mark('answered', status='completed')
        (self.root / '.env').write_text('GEMINI_API_KEY=TOP_SECRET_SOURCE')
        (state / 'bot.log').write_text('PRIVATE_LOG')

    def clone(self, branch='main'):
        target = Path(self.temp.name) / 'target'
        self.cmd('clone', '--branch', branch, str(self.remote), str(target))
        self.cmd('config', 'user.name', 'Target Test', cwd=target)
        self.cmd('config', 'user.email', 'target@example.invalid', cwd=target)
        return target

    def test_round_trip_history_tasks_and_secret_exclusion(self):
        archive = export_state(self.root)
        with zipfile.ZipFile(archive) as bundle:
            self.assertEqual(set(bundle.namelist()), {'manifest.json', 'history.json', 'tasks.json', 'workspace.json'})
            all_data = b''.join(bundle.read(name) for name in bundle.namelist())
            self.assertNotIn(b'TOP_SECRET_SOURCE', all_data)
            self.assertNotIn(b'PRIVATE_LOG', all_data)
        target = self.clone()
        (target / '.env').write_text('GEMINI_API_KEY=TARGET_SECRET')
        restore_state(target, archive)
        self.assertEqual(json.loads((target / '.nova/history.json').read_text()), self.history)
        store = TaskStore(target / '.nova/tasks.sqlite3')
        self.addCleanup(store.close)
        self.assertEqual(store.get(self.task.id)['status'], 'completed')
        self.assertEqual((target / '.env').read_text(), 'GEMINI_API_KEY=TARGET_SECRET')

    def test_live_state_lock_blocks_export(self):
        with state_lock(self.root), self.assertRaises(TaskError):
            export_state(self.root)

    def test_unpushed_commit_blocks_export(self):
        (self.root / 'RESUME.md').write_text('new commit')
        self.cmd('add', 'RESUME.md')
        self.cmd('commit', '-m', 'not pushed')
        with self.assertRaisesRegex(TaskError, 'Push'):
            export_state(self.root)

    def test_unrelated_dirty_file_blocks_export(self):
        (self.root / 'RESUME.md').write_text('unsaved human work')
        with self.assertRaisesRegex(TaskError, 'RESUME.md'):
            export_state(self.root)

    def test_wrong_commit_blocks_restore(self):
        archive = export_state(self.root)
        target = self.clone()
        (target / 'RESUME.md').write_text('newer')
        self.cmd('add', 'RESUME.md', cwd=target)
        self.cmd('commit', '-m', 'newer', cwd=target)
        with self.assertRaisesRegex(TaskError, 'Commit'):
            restore_state(target, archive)
        self.assertFalse((target / '.nova/history.json').exists())

    def test_checksum_tampering_and_path_traversal_rejected(self):
        archive = export_state(self.root)
        with zipfile.ZipFile(archive) as source:
            members = {name: source.read(name) for name in source.namelist()}
        broken = Path(self.temp.name) / 'tampered.zip'
        with zipfile.ZipFile(broken, 'w') as bundle:
            for name, raw in members.items():
                bundle.writestr(name, b'{}' if name == 'history.json' else raw)
        with self.assertRaisesRegex(TaskError, 'checksum'):
            read_archive(self.root, broken)
        with zipfile.ZipFile(broken, 'w') as bundle:
            for name, raw in members.items():
                bundle.writestr(name, raw)
            bundle.writestr('../outside', 'must never extract')
        with self.assertRaisesRegex(TaskError, 'members'):
            read_archive(self.root, broken)
        self.assertFalse((self.root.parent / 'outside').exists())

    def test_existing_state_is_backed_up_before_replace(self):
        archive = export_state(self.root)
        target = self.clone()
        (target / '.nova').mkdir()
        old = {'history': [], 'last_message_id': 5}
        (target / '.nova/history.json').write_text(json.dumps(old))
        backup = restore_state(target, archive)
        self.assertEqual(json.loads((backup / 'history.json').read_text()), old)

    def partial_task(self):
        task = self.store.create(42, 11, 'two files')
        changes = [('game_engine/index.html', '<script src="app.js"></script>'),
                   ('game_engine/app.js', 'const x = 1;')]
        workspace = Workspace(self.root)
        _, snapshot = workspace.snapshot(['game_engine/app.js'])
        calls = 0
        def write(path, content):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise SystemExit('simulated interruption')
            return atomic_write(path, content)
        with patch('nova_batch.atomic_write', side_effect=write), self.assertRaises(SystemExit):
            workspace.save_many(changes, task, snapshot)
        self.cmd('push', 'origin', 'nova/work')
        return task

    def test_partial_multi_file_task_transfers_and_resumes(self):
        task = self.partial_task()
        archive = export_state(self.root)
        target = self.clone('nova/work')
        restore_state(target, archive)
        store = TaskStore(target / '.nova/tasks.sqlite3')
        self.addCleanup(store.close)
        store.recover_startup()
        Workspace(target).resume(Task(store, task.id))
        self.assertEqual((target / 'game_engine/app.js').read_text(), 'const x = 1;')
        self.assertEqual(store.get(task.id)['status'], 'completed')
        self.assertEqual(self.cmd('rev-list', '--count', 'HEAD', cwd=target), '2')

    def test_destination_human_changes_are_preserved(self):
        self.partial_task()
        archive = export_state(self.root)
        target = self.clone('nova/work')
        (target / 'game_engine/index.html').write_text('human edit')
        with self.assertRaisesRegex(TaskError, 'different local'):
            restore_state(target, archive)
        self.assertEqual((target / 'game_engine/index.html').read_text(), 'human edit')

    def test_interrupted_restore_leaves_marker_then_retry_finishes(self):
        archive = export_state(self.root)
        target = self.clone()
        import nova_portability
        replace = nova_portability.os.replace
        def interrupt(source, dest):
            if str(dest).endswith('tasks.sqlite3'):
                raise SystemExit('simulated interruption')
            return replace(source, dest)
        with patch('nova_portability.os.replace', side_effect=interrupt), self.assertRaises(SystemExit):
            restore_state(target, archive)
        self.assertTrue((target / '.nova/restore-in-progress').exists())
        restore_state(target, archive)
        self.assertFalse((target / '.nova/restore-in-progress').exists())
