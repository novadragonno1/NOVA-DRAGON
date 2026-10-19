"""Private state transport. Explicit JSON members; never extract arbitrary ZIP paths."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import uuid
import zipfile

from nova_runtime import TaskError, atomic_write, git, safe_path
from nova_tasks import TaskStore
from scripts.manage_bot import bot_pids

LIMITS = {'manifest.json': 64_000, 'history.json': 8_000_000,
          'tasks.json': 48_000_000, 'workspace.json': 4_000_000}
COLUMNS = ('id', 'chat_id', 'message_id', 'summary', 'status', 'phase', 'details',
           'error', 'created_at', 'updated_at')
STATES = {'running', 'interrupted', 'needs_review', 'awaiting_sync', 'completed', 'failed', 'cancelled'}


def packed(value):
    return json.dumps(value, ensure_ascii=False).encode('utf-8')


def checksum(value):
    return hashlib.sha256(value).hexdigest()


@contextmanager
def state_lock(root):
    root = Path(root).resolve()
    state = root / '.nova'
    if state.is_symlink():
        raise TaskError('.nova must not be a symlink.')
    state.mkdir(mode=0o700, exist_ok=True)
    for name in ('bot.lock', 'tasks.sqlite3', 'history.json', 'restore-in-progress'):
        if (state / name).is_symlink():
            raise TaskError('State files must not be symlinks.')
    if bot_pids(root):
        raise TaskError('Stop NOVA with: python scripts/manage_bot.py stop')
    with (state / 'bot.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise TaskError('NOVA or a state transfer is running. Stop it first.') from None
        yield state


def validate_history(root, history):
    if not isinstance(history, dict) or not isinstance(history.get('history'), list):
        raise TaskError('Invalid history format.')
    if type(history.get('last_message_id')) is not int or history['last_message_id'] < 0:
        raise TaskError('Invalid Telegram message checkpoint.')
    if len(history['history']) > 100:
        raise TaskError('History exceeds transport limits.')
    skills = history.get('skills', [])
    if (not isinstance(skills, list) or len(skills) > 2 or
            any(not isinstance(name, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name) for name in skills)):
        raise TaskError('Invalid selected skills.')
    for item in history['history']:
        if not isinstance(item, dict) or item.get('role') not in ('user', 'model'):
            raise TaskError('Invalid history role.')
        parts = item.get('parts')
        if not isinstance(parts, list) or not all(isinstance(p, dict) and isinstance(p.get('text'), str) for p in parts):
            raise TaskError('Invalid history content.')
    selected = history.get('selected', [])
    if not isinstance(selected, list) or len(selected) > 8:
        raise TaskError('Invalid selected file list.')
    for name in selected:
        if not isinstance(name, str):
            raise TaskError('Invalid selected file.')
        safe_path(root, name)


def validate_tasks(root, rows):
    if not isinstance(rows, list) or len(rows) > 10_000:
        raise TaskError('Invalid task collection.')
    seen_ids, seen_messages = set(), set()
    allowed_files = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(COLUMNS):
            raise TaskError('Unsupported task schema.')
        if not isinstance(row['id'], str) or not re.fullmatch('[a-f0-9]{32}', row['id']):
            raise TaskError('Invalid task identifier.')
        if type(row['chat_id']) is not int or type(row['message_id']) is not int:
            raise TaskError('Invalid task message identifier.')
        message = (row['chat_id'], row['message_id'])
        if row['id'] in seen_ids or message in seen_messages:
            raise TaskError('Duplicate task/message in archive.')
        seen_ids.add(row['id'])
        seen_messages.add(message)
        if not isinstance(row['status'], str) or row['status'] not in STATES or not isinstance(row['details'], dict):
            raise TaskError('Invalid task state.')
        for key in ('summary', 'phase', 'error', 'created_at', 'updated_at'):
            if not isinstance(row[key], str):
                raise TaskError('Invalid task text.')
        files = row['details'].get('files', [])
        if not isinstance(files, list) or len(files) > 8:
            raise TaskError('Invalid task file list.')
        for name in files:
            if not isinstance(name, str):
                raise TaskError('Invalid task path.')
            safe_path(root, name)
            if row['status'] in ('running', 'interrupted', 'needs_review', 'awaiting_sync'):
                allowed_files.add(name)
    if len(allowed_files) > 16:
        raise TaskError('Too many pending files for one transport.')
    return allowed_files


def check_dirty(root, allowed):
    entries = git(root, 'status', '--porcelain', '-z', '--untracked-files=all').split('\0')
    for entry in entries:
        if not entry:
            continue
        code, name = entry[:2], entry[3:]
        # Legacy runtime noise is excluded, never archived.
        if (name.startswith(('.nova/', '.venv/')) or name in ('.env', 'nohup.out')
                or '__pycache__/' in name or name.endswith(('.pyc', '.log'))):
            continue
        if 'R' in code or 'C' in code or name not in allowed:
            raise TaskError('Uncommitted work outside pending tasks must be saved before transport: ' + name)


def collect_tasks(state):
    store = TaskStore(state / 'tasks.sqlite3')
    try:
        rows = []
        for row in store.db.execute('SELECT * FROM tasks ORDER BY rowid'):
            item = dict(row)
            item['details'] = json.loads(item['details'])
            rows.append(item)
        return rows
    finally:
        store.close()


def export_state(root):
    root = Path(root).resolve()
    with state_lock(root) as state:
        if (state / 'restore-in-progress').exists():
            raise TaskError('Finish the previous restore before exporting.')
        head = git(root, 'rev-parse', 'HEAD')
        branch = git(root, 'branch', '--show-current')
        if branch not in ('main', 'nova/work'):
            raise TaskError('Export from main or nova/work only.')
        remote = git(root, 'ls-remote', '--exit-code', 'origin', 'refs/heads/' + branch)
        if not remote or remote.split()[0] != head:
            raise TaskError('Push this branch before exporting; the archive does not contain Git history.')
        rows = collect_tasks(state)
        allowed = validate_tasks(root, rows)
        check_dirty(root, allowed)
        history_path = state / 'history.json'
        history = json.loads(history_path.read_text()) if history_path.exists() else {'history': [], 'last_message_id': 0}
        validate_history(root, history)
        workspace = {}
        for name in sorted(allowed):
            path = safe_path(root, name)
            if not path.exists():
                if git(root, 'ls-files', '--', name):
                    raise TaskError('A tracked pending file was deleted; resolve it before export: ' + name)
                workspace[name] = None
            else:
                if not path.is_file() or path.stat().st_size > 200_000:
                    raise TaskError('Pending file exceeds transport limits: ' + name)
                workspace[name] = path.read_bytes().decode('utf-8')
        payloads = {'history.json': packed(history), 'tasks.json': packed(rows), 'workspace.json': packed(workspace)}
        for name, raw in payloads.items():
            if len(raw) > LIMITS[name]:
                raise TaskError('State exceeds transport size limit: ' + name)
        manifest = {'format': 1, 'head': head, 'branch': branch,
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'checksums': {name: checksum(raw) for name, raw in payloads.items()}}
        if git(root, 'rev-parse', 'HEAD') != head:
            raise TaskError('Git HEAD changed during export; retry after finishing other work.')
        exports = state / 'exports'
        if exports.is_symlink():
            raise TaskError('Export directory must not be a symlink.')
        exports.mkdir(mode=0o700, exist_ok=True)
        archive = exports / ('nova-state-' + uuid.uuid4().hex[:12] + '.zip')
        fd = os.open(archive, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as output, zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr('manifest.json', packed(manifest))
            for name, raw in payloads.items():
                bundle.writestr(name, raw)
        return archive


def read_archive(root, archive):
    archive = Path(archive)
    if archive.stat().st_size > 64_000_000:
        raise TaskError('Archive is too large.')
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len(names) != 4 or set(names) != set(LIMITS):
            raise TaskError('Unexpected or duplicate archive members.')
        payloads = {}
        for name in names:
            if bundle.getinfo(name).file_size > LIMITS[name]:
                raise TaskError('Expanded archive member exceeds size limit.')
            with bundle.open(name) as handle:
                raw = handle.read(LIMITS[name] + 1)
            if len(raw) > LIMITS[name]:
                raise TaskError('Expanded data exceeds size limit.')
            payloads[name] = raw
    manifest = json.loads(payloads.pop('manifest.json'))
    if (not isinstance(manifest, dict) or manifest.get('format') != 1
            or manifest.get('branch') not in ('main', 'nova/work')
            or not re.fullmatch('[a-f0-9]{40}', str(manifest.get('head', '')))):
        raise TaskError('Unsupported or invalid archive manifest.')
    if manifest.get('checksums') != {name: checksum(raw) for name, raw in payloads.items()}:
        raise TaskError('Archive checksum mismatch.')
    history, rows, workspace = (json.loads(payloads[name]) for name in ('history.json', 'tasks.json', 'workspace.json'))
    validate_history(root, history)
    allowed = validate_tasks(root, rows)
    if not isinstance(workspace, dict) or set(workspace) != allowed:
        raise TaskError('Workspace snapshot does not match pending tasks.')
    for name, content in workspace.items():
        safe_path(root, name)
        if content is not None and (not isinstance(content, str) or len(content.encode('utf-8')) > 200_000):
            raise TaskError('Invalid pending file content.')
    return manifest, history, rows, workspace


def restore_state(root, archive):
    root = Path(root).resolve()
    # Fully inspect without writing to the destination first.
    manifest, history, rows, workspace = read_archive(root, archive)
    with state_lock(root) as state:
        if git(root, 'rev-parse', 'HEAD') != manifest['head'] or git(root, 'branch', '--show-current') != manifest['branch']:
            raise TaskError('Restore requires branch ' + manifest['branch'] + ' at Commit ' + manifest['head'] + '. No files changed.')
        if git(root, 'diff', '--cached', '--name-only'):
            raise TaskError('Finish or unstage existing changes before restore; nothing was overwritten.')
        check_dirty(root, set(workspace))
        for name, content in workspace.items():
            path = safe_path(root, name)
            dirty = git(root, 'status', '--porcelain', '--', name)
            if dirty and (not path.is_file() or path.read_bytes().decode('utf-8') != content):
                raise TaskError('Destination has different local changes: ' + name)
            if content is None and path.exists():
                raise TaskError('Archive expects a new file to be absent: ' + name)
        backups = state / 'backups'
        if backups.is_symlink():
            raise TaskError('Backup directory must not be a symlink.')
        backups.mkdir(mode=0o700, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix='before-restore-', dir=backups))
        if (state / 'history.json').exists():
            atomic_write(backup / 'history.json', (state / 'history.json').read_text())
        old_db = state / 'tasks.sqlite3'
        if old_db.exists():
            source = sqlite3.connect(old_db)
            target = sqlite3.connect(backup / 'tasks.sqlite3')
            try:
                source.backup(target)
            finally:
                source.close()
                target.close()
        if any((state / ('tasks.sqlite3' + suffix)).exists() for suffix in ('-wal', '-shm', '-journal')):
            raise TaskError('SQLite journal files remain; resolve them before restoring.')
        with tempfile.TemporaryDirectory(prefix='restore-', dir=state) as temp:
            staged = Path(temp)
            store = TaskStore(staged / 'tasks.sqlite3')
            try:
                with store.db:
                    for row in rows:
                        values = [json.dumps(row[col], ensure_ascii=False) if col == 'details' else row[col] for col in COLUMNS]
                        store.db.execute('INSERT INTO tasks (' + ','.join(COLUMNS) + ') VALUES (' + ','.join('?' for _ in COLUMNS) + ')', values)
            finally:
                store.close()
            atomic_write(staged / 'history.json', json.dumps(history, ensure_ascii=False))
            marker = state / 'restore-in-progress'
            atomic_write(marker, json.dumps({'backup': str(backup), 'archive': str(Path(archive).resolve())}))
            # A process crash here leaves the marker, preventing startup until restore is repeated.
            for name, content in workspace.items():
                if content is not None:
                    atomic_write(safe_path(root, name), content)
            os.replace(staged / 'tasks.sqlite3', old_db)
            os.replace(staged / 'history.json', state / 'history.json')
            marker.unlink()
        return backup
