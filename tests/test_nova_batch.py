import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_nova_runtime as runtime_tests
from nova_batch import parse_changes
from nova_runtime import TaskError, atomic_write
from nova_tasks import TaskStore
from nova_validation import validate_bundle


class ParseAndValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def envelope(self, files):
        return '[FILES]\n```json\n' + json.dumps({'files': files}) + '\n```'

    def test_multi_file_parse_and_legacy_format(self):
        files = [{'path': 'game_engine/a.js', 'content': 'const a = 1;'},
                 {'path': 'game_engine/b.json', 'content': '{"b": 2}'}]
        self.assertEqual(parse_changes(self.envelope(files)), [(x['path'], x['content']) for x in files])
        self.assertEqual(parse_changes('[SAVE: RESUME.md]\n```md\nsummary\n```'), [('RESUME.md', 'summary\n')])

    def test_duplicate_paths_and_json_keys_rejected(self):
        files = [{'path': 'game_engine/a.js', 'content': 'a'}] * 2
        for answer in (self.envelope(files), '[FILES]\n```json\n{"files":[],"files":[]}\n```'):
            with self.subTest(answer=answer), self.assertRaises(TaskError):
                parse_changes(answer)

    def test_too_many_files_and_invalid_envelope_rejected(self):
        files = [{'path': f'game_engine/a{i}.md', 'content': 'a'} for i in range(9)]
        for answer in (self.envelope(files), '[FILES]\n```json\n{"files":"not a list"}\n```'):
            with self.assertRaises(TaskError):
                parse_changes(answer)

    def test_js_syntax_error_blocks_validation(self):
        with self.assertRaisesRegex(TaskError, 'JavaScript'):
            validate_bundle(self.root, [('game_engine/a.js', 'const = ;')])

    def test_js_is_parsed_but_never_executed(self):
        # If executed, this would always throw; --check only parses it.
        reports = validate_bundle(self.root, [('game_engine/a.js', 'throw new Error("must not execute");')])
        self.assertIn('ناجح', reports[0])

    def test_missing_node_blocks_js_instead_of_claiming_success(self):
        with patch('nova_validation.shutil.which', return_value=None), self.assertRaisesRegex(TaskError, 'Node.js'):
            validate_bundle(self.root, [('game_engine/a.js', 'const x = 1;')])

    def test_html_resolves_sibling_files_in_same_bundle(self):
        reports = validate_bundle(self.root, [
            ('game_engine/index.html', '<script type="module" src="app.js"></script><link rel="stylesheet" href="style.css">'),
            ('game_engine/app.js', 'export const answer = 42;'),
            ('game_engine/style.css', 'body { margin: 0; }')])
        self.assertEqual(len(reports), 3)

    def test_missing_local_html_reference_and_bad_inline_script_rejected(self):
        for content in ('<script src="missing.js"></script>', '<script>const = ;</script>',
                        '<script type="importmap">not json</script>', '<script>const x = 1;'):
            with self.subTest(content=content), self.assertRaises(TaskError):
                validate_bundle(self.root, [('game_engine/index.html', content)])

    def test_other_formats_report_limits(self):
        report = validate_bundle(self.root, [('game_engine/style.css', 'body {}')])[0]
        self.assertIn('لا يوجد محلل', report)

    def test_non_json_numeric_constants_rejected(self):
        for content in ('{"value": NaN}', '{"value": Infinity}'):
            with self.assertRaises(TaskError):
                validate_bundle(self.root, [('game_engine/settings.json', content)])


class BatchGitTests(unittest.TestCase):
    cmd = runtime_tests.GitTests.cmd

    def setUp(self):
        runtime_tests.GitTests.setUp(self)
        self.store = TaskStore(self.root / '.nova/tasks.sqlite3')
        self.addCleanup(self.store.close)
        self.task = self.store.create(42, 100, 'two files')
        self.changes = [('game_engine/index.html', '<script src="app.js"></script>'),
                        ('game_engine/app.js', 'const answer = 42;')]
        _, self.snapshot = self.workspace.snapshot(['game_engine/app.js'])

    def test_all_files_saved_in_one_commit_and_pushed(self):
        self.workspace.save_many(self.changes, self.task, self.snapshot)
        self.assertEqual(self.cmd('rev-list', '--count', 'HEAD'), '2')
        self.assertEqual(set(self.cmd('show', '--pretty=', '--name-only', 'HEAD').splitlines()),
                         {'game_engine/index.html', 'game_engine/app.js'})
        self.assertEqual(self.cmd('rev-parse', 'main'), self.base)
        self.assertEqual(self.cmd('rev-parse', 'nova/work', cwd=self.remote), self.cmd('rev-parse', 'HEAD'))
        row = self.store.get(self.task.id)
        self.assertEqual(row['status'], 'completed')
        self.assertIsNone(row['details']['batch'])
        self.assertIn('JavaScript', row['details']['validation'])

    def test_invalid_second_file_does_not_change_first_file(self):
        bad = [self.changes[0], ('game_engine/app.js', 'const = ;')]
        with self.assertRaises(TaskError):
            self.workspace.save_many(bad, self.task, self.snapshot)
        self.assertEqual((self.root / 'game_engine/index.html').read_text(), 'old')
        self.assertFalse((self.root / 'game_engine/app.js').exists())
        self.assertEqual(self.cmd('branch', '--show-current'), 'main')
        self.assertIn('فشل', self.store.get(self.task.id)['details']['validation'])

    def test_file_changed_after_read_is_preserved(self):
        (self.root / 'game_engine/index.html').write_text('human change')
        with self.assertRaises(TaskError):
            self.workspace.save_many(self.changes, self.task, self.snapshot)
        self.assertEqual((self.root / 'game_engine/index.html').read_text(), 'human change')

    def test_unread_file_rejected(self):
        _, snapshot = self.workspace.snapshot()
        with self.assertRaisesRegex(TaskError, '/read'):
            self.workspace.save_many(self.changes, self.task, snapshot)

    def partial_write(self):
        count = 0
        def write(path, content):
            nonlocal count
            count += 1
            if count == 2:
                raise SystemExit('simulated interruption between files')
            return atomic_write(path, content)
        with patch('nova_batch.atomic_write', side_effect=write), self.assertRaises(SystemExit):
            self.workspace.save_many(self.changes, self.task, self.snapshot)

    def test_partial_batch_resumes_without_rewriting_completed_file(self):
        self.partial_write()
        self.assertEqual(self.cmd('rev-parse', 'HEAD'), self.base)
        self.store.recover_startup()
        written = []
        def write(path, content):
            written.append(path.name)
            return atomic_write(path, content)
        with patch('nova_batch.atomic_write', side_effect=write):
            self.workspace.resume(self.task)
        self.assertEqual(written, ['app.js'])
        self.assertEqual(self.store.get(self.task.id)['status'], 'completed')
        self.assertEqual(self.cmd('rev-list', '--count', 'HEAD'), '2')

    def test_partial_batch_refuses_human_edit(self):
        self.partial_write()
        (self.root / 'game_engine/index.html').write_text('human correction')
        with self.assertRaises(TaskError):
            self.workspace.resume(self.task)
        self.assertFalse((self.root / 'game_engine/app.js').exists())
        self.assertEqual((self.root / 'game_engine/index.html').read_text(), 'human correction')

    def test_crash_after_commit_does_not_duplicate_batch(self):
        mark = self.task.mark
        def checkpoint(phase, *args, **kwargs):
            if phase == 'committed':
                raise SystemExit('crash before ledger checkpoint')
            return mark(phase, *args, **kwargs)
        with patch.object(self.task, 'mark', side_effect=checkpoint), self.assertRaises(SystemExit):
            self.workspace.save_many(self.changes, self.task, self.snapshot)
        sha = self.cmd('rev-parse', 'HEAD')
        self.workspace.resume(self.task)
        self.assertEqual(self.cmd('rev-parse', 'HEAD'), sha)
        self.assertEqual(self.store.get(self.task.id)['status'], 'completed')

    def test_push_failure_recovers_whole_batch(self):
        self.cmd('remote', 'set-url', 'origin', str(self.remote) + '-missing')
        with self.assertRaises(TaskError) as error:
            self.workspace.save_many(self.changes, self.task, self.snapshot)
        self.task.fail(str(error.exception))
        self.assertEqual(self.store.get(self.task.id)['status'], 'awaiting_sync')
        self.cmd('remote', 'set-url', 'origin', str(self.remote))
        self.workspace.resume(self.task)
        self.assertEqual(self.cmd('show', 'nova/work:game_engine/app.js', cwd=self.remote), 'const answer = 42;')


if __name__ == '__main__':
    unittest.main()
