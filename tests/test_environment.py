from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import manage_bot, setup_env


class EnvironmentTests(unittest.TestCase):
    def test_process_scan_only_matches_this_workspace_bot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'project'
            root.mkdir()
            proc = Path(tmp) / 'proc'
            proc.mkdir()
            for pid, args, cwd in [
                (900001, ['python', '-u', 'bot.py'], root),
                (900002, ['python', '-c', 'bot.py'], root),
                (900003, ['python', 'bot.py'], Path(tmp)),
                (900004, ['python', 'other.py', 'bot.py'], root),
            ]:
                item = proc / str(pid)
                item.mkdir()
                (item / 'cmdline').write_bytes(('\0'.join(args) + '\0').encode())
                (item / 'cwd').symlink_to(cwd, target_is_directory=True)
            with patch.object(manage_bot, 'PROC_ROOT', proc):
                self.assertEqual(manage_bot.bot_pids(root), [900001])

    def test_duplicate_start_does_not_launch_process(self):
        with patch.object(manage_bot, 'bot_pids', return_value=[123]), patch.object(manage_bot.subprocess, 'Popen') as launch:
            manage_bot.start(Path('.'))
            launch.assert_not_called()

    def test_restore_marker_blocks_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.nova').mkdir()
            (root / '.nova/restore-in-progress').touch()
            with patch.object(manage_bot, 'bot_pids', return_value=[]), self.assertRaisesRegex(RuntimeError, 'restore'):
                manage_bot.start(root)

    def test_setup_reuses_venv_preserves_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.venv/bin').mkdir(parents=True)
            (root / '.venv/bin/python').touch()
            (root / '.env').write_text('private value')
            with patch.object(setup_env, 'ROOT', root), patch.object(setup_env.shutil, 'which', return_value='/tool'), patch.object(setup_env.subprocess, 'run') as run:
                setup_env.main()
                self.assertEqual(run.call_count, 2)
                self.assertEqual(run.call_args.args[0][0], str(root / '.venv/bin/python'))
            self.assertEqual((root / '.env').read_text(), 'private value')

    def test_setup_missing_node_stops_before_install(self):
        with patch.object(setup_env.shutil, 'which', return_value=None), patch.object(setup_env.subprocess, 'run') as run, self.assertRaises(SystemExit):
            setup_env.main()
        run.assert_not_called()
