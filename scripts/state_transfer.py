"""Usage: export | inspect ARCHIVE | restore ARCHIVE. Stop the bot first."""
import argparse
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nova_portability import export_state, read_archive, restore_state
from nova_runtime import TaskError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['export', 'inspect', 'restore'])
    parser.add_argument('archive', nargs='?')
    args = parser.parse_args()
    if args.action != 'export' and not args.archive:
        parser.error('archive path is required')
    try:
        if args.action == 'export':
            path = export_state(ROOT)
            print('Private state archive: ' + str(path))
            print('Download it privately. .env, logs and Git history are not included.')
        elif args.action == 'inspect':
            manifest, _, tasks, workspace = read_archive(ROOT, args.archive)
            print('Required branch: ' + manifest['branch'])
            print('Required Commit: ' + manifest['head'])
            print(f'Tasks: {len(tasks)}; pending workspace files: {len(workspace)}')
        else:
            backup = restore_state(ROOT, args.archive)
            print('State restored. Previous local state backup: ' + str(backup))
            print('Configure credentials separately, then run python scripts/manage_bot.py start')
    except (TaskError, OSError, ValueError, zipfile.BadZipFile) as error:
        raise SystemExit(str(error)) from None


if __name__ == '__main__':
    main()
