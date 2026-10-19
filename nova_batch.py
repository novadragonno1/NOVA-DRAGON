"""Validated, recoverable multi-file edits with a single Git commit."""
import hashlib
import json
from pathlib import Path
import re

from nova_runtime import TaskError, atomic_write, git, parse_save, safe_path, validate_content
from nova_validation import validate_bundle


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def parse_changes(answer):
    if '[FILES]' not in answer:
        single = parse_save(answer)
        return [single] if single else None
    match = re.fullmatch(r'\s*\[FILES\]\s*```json\s*\n(.*?)\n```\s*', answer, re.S)
    if not match or '[SAVE:' in answer:
        raise TaskError('استخدم [FILES] وكتلة JSON واحدة دون خلط صيغ الحفظ.')
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    try:
        data = json.loads(match[1], object_pairs_hook=unique_pairs)
        if not isinstance(data, dict) or set(data) != {'files'} or not isinstance(data['files'], list):
            raise ValueError('invalid envelope')
        changes = []
        for item in data['files']:
            if not isinstance(item, dict) or set(item) != {'path', 'content'}:
                raise ValueError('invalid file')
            if not isinstance(item['path'], str) or not isinstance(item['content'], str):
                raise ValueError('invalid types')
            changes.append((item['path'], item['content']))
    except (ValueError, TypeError):
        raise TaskError('بنية JSON للتعديلات غير صالحة؛ لم يُحفظ شيء.') from None
    check_changes(changes)
    return changes


def check_changes(changes):
    if not 1 <= len(changes) <= 8 or len({n for n, _ in changes}) != len(changes):
        raise TaskError('المطلوب من ملف واحد إلى 8 ملفات دون تكرار.')
    if sum(len(c.encode('utf-8')) for _, c in changes) > 500_000:
        raise TaskError('مجموع التعديلات أكبر من 500 كيلوبايت.')
    for name, content in changes:
        validate_content(name, content)


def check_snapshot(workspace, snapshot):
    for name, expected in snapshot.items():
        if fingerprint(safe_path(workspace.root, name)) != expected:
            raise TaskError('تغير ملف السياق أثناء إعداد الرد: ' + name + '؛ أعد قراءة الملفات.')


def save_many(workspace, changes, task, snapshot):
    check_changes(changes)
    for name, _ in changes:
        safe_path(workspace.root, name)
        if name not in snapshot:
            raise TaskError('اقرأ الملف أولًا باستخدام /read ' + name)
    check_snapshot(workspace, snapshot)
    try:
        reports = validate_bundle(workspace.root, changes)
    except TaskError as error:
        task.store.update(task.id, validation='فشل الفحص: ' + str(error))
        raise
    task.mark('validated', validation='\n'.join(reports))
    if git(workspace.root, 'rev-parse', '--show-toplevel') != str(workspace.root):
        raise TaskError('مجلد العمل ليس جذر المستودع.')
    if git(workspace.root, 'diff', '--cached', '--name-only'):
        raise TaskError('توجد ملفات مجهزة لعمل آخر؛ لم يُحفظ شيء.')
    names = [name for name, _ in changes]
    if git(workspace.root, 'status', '--porcelain', '--', *names):
        raise TaskError('توجد تعديلات محلية في الملفات المستهدفة؛ لم أستبدلها.')
    branch = git(workspace.root, 'branch', '--show-current')
    if branch == 'main':
        if git(workspace.root, 'branch', '--list', 'nova/work'):
            raise TaskError('فرع nova/work موجود؛ راجع وضعه قبل الانتقال إليه.')
        git(workspace.root, 'switch', '-c', 'nova/work')
    elif branch != 'nova/work':
        raise TaskError('التعديل متاح على main لإنشاء الفرع أو nova/work فقط.')
    check_snapshot(workspace, snapshot)
    batch = [{'path': name, 'content': content, 'before_hash': snapshot[name],
              'after_hash': hashlib.sha256(content.encode('utf-8')).hexdigest()}
             for name, content in changes]
    # Store the entire validated proposal BEFORE the first write. It stays private in SQLite.
    task.mark('prepared', files=names, batch=batch, base_commit=git(workspace.root, 'rev-parse', 'HEAD'),
              validation='\n'.join(reports), context_snapshot=snapshot)
    finish_batch(workspace, task, reports=reports)
    return task.store.summary(task.id) + '\nالفرع: nova/work؛ لا دمج تلقائي في main.'


def finish_batch(workspace, task, reports=None):
    data = task.store.get(task.id)['details']
    batch = data['batch']
    if git(workspace.root, 'branch', '--show-current') != 'nova/work':
        raise TaskError('استكمال المهمة يحتاج فرع nova/work.')
    if git(workspace.root, 'rev-parse', 'HEAD') != data['base_commit']:
        raise TaskError('تغير تاريخ الفرع؛ لم أكتب أو أحفظ ملفات إضافية.')
    names = [item['path'] for item in batch]
    staged = git(workspace.root, 'diff', '--cached', '--name-only').splitlines()
    if any(name not in names for name in staged):
        raise TaskError('توجد ملفات في staging خارج المهمة؛ تحتاج مراجعة.')
    for name, expected in data.get('context_snapshot', {}).items():
        if name not in names and fingerprint(safe_path(workspace.root, name)) != expected:
            raise TaskError('تغير ملف سياق غير مستهدف؛ تحتاج مراجعة: ' + name)
    for item in batch:
        path = safe_path(workspace.root, item['path'])
        if fingerprint(path) not in (item['before_hash'], item['after_hash']):
            raise TaskError('تغير الملف بعد إعداد المهمة؛ لم أستبدله: ' + item['path'])
        if item['path'] in staged and git(workspace.root, 'rev-parse', ':' + item['path']) != git(
                workspace.root, 'hash-object', '--', item['path']):
            raise TaskError('نسخة staging مختلفة؛ لم أستبدلها.')
    # Check the preserved bundle again before resuming any partial write.
    if reports is None:
        reports = validate_bundle(workspace.root, [(x['path'], x['content']) for x in batch])
    for item in batch:
        path = safe_path(workspace.root, item['path'])
        current = fingerprint(path)
        if current == item['after_hash']:
            continue
        if current != item['before_hash']:
            raise TaskError('تغير ملف أثناء الحفظ؛ توقف التنفيذ: ' + item['path'])
        atomic_write(path, item['content'])
    task.mark('saved', validation='\n'.join(reports))
    for item in batch:
        if fingerprint(safe_path(workspace.root, item['path'])) != item['after_hash']:
            raise TaskError('تغير ملف قبل Commit؛ لم يتم إنشاء Commit.')
    git(workspace.root, 'add', '--', *names)
    if not git(workspace.root, 'diff', '--cached', '--name-only'):
        task.mark('unchanged', status='completed', batch=None)
        return
    git(workspace.root, 'commit', '-m', f'NOVA: update {len(names)} file(s)\n\nNOVA-Task: {task.id}')
    sha = git(workspace.root, 'rev-parse', 'HEAD')
    task.mark('committed', commit=sha)
    workspace.sync()
    task.mark('pushed', status='completed', batch=None)


def resume_batch(workspace, task):
    data = task.store.get(task.id)['details']
    if git(workspace.root, 'branch', '--show-current') != 'nova/work':
        raise TaskError('الاستئناف يحتاج فرع nova/work.')
    found = git(workspace.root, 'log', '--format=%H', '--fixed-strings',
                '--grep=NOVA-Task: ' + task.id).splitlines()
    if len(found) > 1:
        raise TaskError('أكثر من Commit للمهمة؛ تحتاج مراجعة.')
    sha = found[0] if found else data.get('commit')
    if sha:
        git(workspace.root, 'merge-base', '--is-ancestor', sha, 'HEAD')
        task.mark('committed', commit=sha)
        workspace.sync()
        task.mark('pushed', status='completed', batch=None)
    else:
        finish_batch(workspace, task)
    return task.store.summary(task.id)
