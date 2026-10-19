"""Small, auditable file writer. No generated code is executed here."""
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import hashlib


class TaskError(Exception):
    pass


def authorized(message, owner_id):
    return (getattr(getattr(message, 'chat', None), 'type', None) == 'private'
            and getattr(getattr(message, 'from_user', None), 'id', None) == owner_id)


def safe_path(root, name):
    root = Path(root).resolve()
    if not re.fullmatch(r'[A-Za-z0-9_./-]{1,200}', name):
        raise TaskError('اسم الملف غير مسموح.')
    parts = PurePosixPath(name).parts
    if (not parts or name != str(PurePosixPath(name)) or name.startswith('/') or '\\' in name
            or any(p.startswith('.') for p in parts)):
        raise TaskError('المسار غير مسموح.')
    suffixes = {'.html', '.js', '.ts', '.css', '.json', '.md', '.txt', '.glsl', '.wgsl'}
    if name != 'RESUME.md' and not (
            parts[0] == 'game_engine' and len(parts) > 1
            and Path(name).suffix.lower() in suffixes):
        raise TaskError('الكتابة متاحة داخل game_engine أو RESUME.md فقط.')
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise TaskError('الروابط الرمزية غير مسموحة.')
    if not current.resolve().is_relative_to(root):
        raise TaskError('المسار خارج المشروع.')
    return current


def parse_save(answer):
    markers = re.findall(r'\[SAVE:\s*([^\]\n]+)\]', answer)
    if not markers:
        return None
    blocks = re.findall(r'^```[^\n]*\n(.*?)^```\s*$', answer, re.M | re.S)
    if len(markers) != 1 or len(blocks) != 1:
        raise TaskError('المطلوب ملف واحد وكتلة كود واحدة. لم يُحفظ شيء.')
    content = blocks[0]
    name = markers[0].strip()
    validate_content(name, content)
    return name, content


def validate_content(name, content):
    if not isinstance(content, str):
        raise TaskError('محتوى الملف يجب أن يكون نصًا.')
    if not content.strip() or len(content.encode('utf-8')) > 200_000:
        raise TaskError('الملف فارغ أو أكبر من الحد المسموح.')
    # Catch common credential formats before publishing generated content.
    if re.search(r'gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|'
                 r'AIza[A-Za-z0-9_-]{30,}|\d{7,12}:[A-Za-z0-9_-]{30,}', content):
        raise TaskError('المحتوى يشبه مفتاح وصول. أُوقف الحفظ.')
    if name.lower().endswith('.json'):
        try:
            strict_json(content)
        except ValueError:
            raise TaskError('ملف JSON غير صالح. لم يُحفظ شيء.') from None


def strict_json(content):
    def reject_constant(value):
        raise ValueError('Non-JSON numeric constant')
    return json.loads(content, parse_constant=reject_constant)


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.nova-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def git(root, *args):
    try:
        result = subprocess.run(['git', *args], cwd=root, capture_output=True,
                                text=True, timeout=90, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise TaskError('تعذر إكمال عملية Git؛ افحص الحالة قبل إعادة المحاولة.') from None
    if result.returncode:
        # Never expose raw stderr: remote URLs may contain credentials.
        raise TaskError('فشلت عملية Git (%s). لم يتم تأكيد المزامنة.' % args[0])
    return result.stdout.strip()


class Workspace:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def context(self, extra=None):
        return self.snapshot(extra)[0]

    def catalog(self):
        result = []
        game = self.root / 'game_engine'
        if game.is_symlink():
            raise TaskError('مجلد اللعبة رابط رمزي غير مسموح.')
        for path in game.rglob('*'):
            if not path.is_file():
                continue
            name = path.relative_to(self.root).as_posix()
            try:
                safe_path(self.root, name)
            except TaskError:
                continue
            result.append(name)
            if len(result) >= 200:
                break
        return '\n'.join(sorted(result)) or 'لا توجد ملفات لعبة نصية بعد.'

    def snapshot(self, extra=None):
        names = ['RESUME.md', 'game_engine/index.html']
        extras = [extra] if isinstance(extra, str) else (extra or [])
        if len(extras) > 8:
            raise TaskError('اقرأ حتى 8 ملفات إضافية في الطلب الواحد.')
        for name in extras:
            if name not in names:
                names.append(name)
        result = []
        fingerprints = {}
        total = 0
        for name in names:
            path = safe_path(self.root, name)
            if path.is_file():
                if path.stat().st_size > 200_000:
                    raise TaskError('ملف السياق كبير؛ قسم المهمة إلى أجزاء أصغر.')
                raw = path.read_bytes()
                content = raw.decode('utf-8')
                fingerprints[name] = hashlib.sha256(raw).hexdigest()
                total += len(raw)
                if total > 500_000:
                    raise TaskError('السياق أكبر من الحد المسموح.')
                result.append(f'FILE: {name}\n{content}')
            else:
                fingerprints[name] = None
                result.append(f'FILE: {name}\n[NEW FILE: does not exist]')
        return '\n\n'.join(result), fingerprints

    def save_many(self, changes, task, snapshot):
        from nova_batch import save_many
        return save_many(self, changes, task, snapshot)

    def save(self, name, content, task=None):
        path = safe_path(self.root, name)
        if git(self.root, 'rev-parse', '--show-toplevel') != str(self.root):
            raise TaskError('مجلد العمل ليس جذر المستودع.')
        if git(self.root, 'diff', '--cached', '--name-only'):
            raise TaskError('توجد ملفات مجهزة لحفظ آخر. أكملها قبل هذه المهمة.')
        if git(self.root, 'status', '--porcelain', '--', name):
            raise TaskError('الملف يحتوي تعديلات محلية غير محفوظة؛ لم أستبدلها.')
        branch = git(self.root, 'branch', '--show-current')
        if branch == 'main':
            if git(self.root, 'branch', '--list', 'nova/work'):
                raise TaskError('فرع nova/work موجود. راجع وضعه قبل الانتقال إليه.')
            git(self.root, 'switch', '-c', 'nova/work')
        elif branch != 'nova/work':
            raise TaskError('التعديل متاح على main لإنشاء فرع، أو على nova/work فقط.')
        if task:
            task.mark('prepared', files=[name], base_commit=git(self.root, 'rev-parse', 'HEAD'),
                      before_hash=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None,
                      content_hash=hashlib.sha256(content.encode('utf-8')).hexdigest(),
                      validation='فحص المسار وصيغة SAVE؛ JSON عند انطباقه')
        atomic_write(path, content)
        if task:
            task.mark('saved')
        git(self.root, 'add', '--', name)
        if not git(self.root, 'diff', '--cached', '--name-only'):
            if task:
                task.mark('unchanged', status='completed')
            return 'لا يوجد تغيير جديد؛ لم أنشئ Commit أو أؤكد رفعًا جديدًا.'
        message = f'NOVA: update {name}'
        if task:
            message += f'\n\nNOVA-Task: {task.id}'
        git(self.root, 'commit', '-m', message)
        sha = git(self.root, 'rev-parse', 'HEAD')
        if task:
            task.mark('committed', commit=sha)
        try:
            git(self.root, 'push', 'origin', 'HEAD:refs/heads/nova/work')
        except TaskError:
            raise TaskError(f'حُفظ التعديل محليًا: {sha}\nفشل الرفع. استخدم /sync لإعادته.') from None
        if task:
            task.mark('pushed', status='completed')
        return (f'تم حفظ ورفع {name}\nالفرع: nova/work\nCommit: {sha}\n'
                'لم يُدمج في main. لم تُشغّل اختبارات اللعبة تلقائيًا.')

    def resume(self, task):
        """Reconcile actual files/Git; never call the model or rewrite saved content."""
        row = task.store.get(task.id)
        if row['status'] in ('completed', 'cancelled'):
            return task.store.summary(task.id)
        data = row['details']
        if data.get('batch'):
            from nova_batch import resume_batch
            return resume_batch(self, task)
        if not data.get('files'):
            task.store.update(task.id, status='failed', error='انقطع الطلب قبل حفظ ملف؛ أعد إرسال الطلب إن لزم.')
            return task.store.summary(task.id)
        if git(self.root, 'branch', '--show-current') != 'nova/work':
            raise TaskError('الاستئناف يحتاج فرع nova/work؛ لم أغيّر الفرع أو الملفات.')
        name = data['files'][0]
        path = safe_path(self.root, name)
        # The unique trailer closes the crash window between commit and ledger update.
        found = git(self.root, 'log', '--format=%H', '--fixed-strings',
                    '--grep=NOVA-Task: ' + task.id).splitlines()
        if len(found) > 1:
            raise TaskError('أكثر من Commit يحمل رقم المهمة؛ تحتاج مراجعة.')
        sha = found[0] if found else data.get('commit')
        if sha:
            # Ensure the recorded commit belongs to the current work history.
            git(self.root, 'merge-base', '--is-ancestor', sha, 'HEAD')
            task.mark('committed', commit=sha)
            self.sync()
            task.mark('pushed', status='completed')
            return task.store.summary(task.id)
        if git(self.root, 'rev-parse', 'HEAD') != data['base_commit']:
            raise TaskError('تغير تاريخ الفرع منذ المهمة؛ نحتاج مراجعة قبل الاستئناف.')
        current_hash = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        staged = git(self.root, 'diff', '--cached', '--name-only').splitlines()
        if any(item != name for item in staged):
            raise TaskError('توجد ملفات مجهزة لمهمة أخرى؛ لم أغيّرها.')
        if current_hash == data.get('before_hash') and current_hash != data['content_hash']:
            if staged:
                raise TaskError('الملف الأصلي موجود مع تغييرات في staging؛ تحتاج مراجعة.')
            task.store.update(task.id, status='failed', error='لم يبقَ تغيير محفوظ لهذه المهمة؛ أعد إرسالها إن لزم.')
            return task.store.summary(task.id)
        if current_hash != data['content_hash']:
            raise TaskError('محتوى الملف تغير بعد الانقطاع؛ لم أستبدل تعديلك.')
        if staged:
            index_hash = git(self.root, 'rev-parse', ':' + name)
            work_hash = git(self.root, 'hash-object', '--', name)
            if index_hash != work_hash:
                raise TaskError('نسخة staging تختلف عن الملف؛ تحتاج مراجعة.')
        git(self.root, 'add', '--', name)
        if not git(self.root, 'diff', '--cached', '--name-only'):
            task.mark('unchanged', status='completed')
            return task.store.summary(task.id)
        git(self.root, 'commit', '-m', f'NOVA: update {name}\n\nNOVA-Task: {task.id}')
        task.mark('committed', commit=git(self.root, 'rev-parse', 'HEAD'))
        self.sync()
        task.mark('pushed', status='completed')
        return task.store.summary(task.id)

    def sync(self):
        if git(self.root, 'branch', '--show-current') != 'nova/work':
            raise TaskError('المزامنة اليدوية مخصصة لفرع nova/work.')
        if git(self.root, 'status', '--porcelain', '--', 'game_engine', 'RESUME.md'):
            raise TaskError('توجد تغييرات غير محفوظة. المزامنة ترفع الـCommits فقط.')
        git(self.root, 'push', 'origin', 'HEAD:refs/heads/nova/work')
        return 'تم رفع فرع nova/work. Commit: ' + git(self.root, 'rev-parse', 'HEAD')
