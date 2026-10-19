"""Small, auditable file writer. No generated code is executed here."""
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile


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
    if (not parts or name.startswith('/') or '\\' in name
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
    if not content.strip() or len(content.encode('utf-8')) > 200_000:
        raise TaskError('الملف فارغ أو أكبر من الحد المسموح.')
    # Catch common credential formats before publishing generated content.
    if re.search(r'gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|'
                 r'AIza[A-Za-z0-9_-]{30,}|\d{7,12}:[A-Za-z0-9_-]{30,}', content):
        raise TaskError('المحتوى يشبه مفتاح وصول. أُوقف الحفظ.')
    name = markers[0].strip()
    if name.endswith('.json'):
        try:
            json.loads(content)
        except ValueError:
            raise TaskError('ملف JSON غير صالح. لم يُحفظ شيء.') from None
    return name, content


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
        names = ['RESUME.md', 'game_engine/index.html']
        if extra and extra not in names:
            names.append(extra)
        result = []
        total = 0
        for name in names:
            path = safe_path(self.root, name)
            if path.is_file():
                if path.stat().st_size > 80_000:
                    raise TaskError('ملف السياق كبير؛ قسم المهمة إلى أجزاء أصغر.')
                content = path.read_text(encoding='utf-8')
                total += len(content.encode('utf-8'))
                if total > 160_000:
                    raise TaskError('السياق أكبر من الحد المسموح.')
                result.append(f'FILE: {name}\n{content}')
        return '\n\n'.join(result)

    def save(self, name, content):
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
        atomic_write(path, content)
        git(self.root, 'add', '--', name)
        if not git(self.root, 'diff', '--cached', '--name-only'):
            return 'لا يوجد تغيير جديد؛ لم أنشئ Commit أو أؤكد رفعًا جديدًا.'
        git(self.root, 'commit', '-m', f'NOVA: update {name}')
        sha = git(self.root, 'rev-parse', 'HEAD')
        try:
            git(self.root, 'push', 'origin', 'HEAD:refs/heads/nova/work')
        except TaskError:
            raise TaskError(f'حُفظ التعديل محليًا: {sha}\nفشل الرفع. استخدم /sync لإعادته.') from None
        return (f'تم حفظ ورفع {name}\nالفرع: nova/work\nCommit: {sha}\n'
                'لم يُدمج في main. لم تُشغّل اختبارات اللعبة تلقائيًا.')

    def sync(self):
        if git(self.root, 'branch', '--show-current') != 'nova/work':
            raise TaskError('المزامنة اليدوية مخصصة لفرع nova/work.')
        if git(self.root, 'status', '--porcelain', '--', 'game_engine', 'RESUME.md'):
            raise TaskError('توجد تغييرات غير محفوظة. المزامنة ترفع الـCommits فقط.')
        git(self.root, 'push', 'origin', 'HEAD:refs/heads/nova/work')
        return 'تم رفع فرع nova/work. Commit: ' + git(self.root, 'rev-parse', 'HEAD')
