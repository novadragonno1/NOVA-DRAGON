"""Read-only Agent Skills bridge. No dynamic imports, shell, or implicit tools."""
import hashlib
from pathlib import Path
import re

from nova_runtime import TaskError

SKILL_LIMIT = 48_000
CONTEXT_LIMIT = 72_000
MAX_SELECTED = 2


class AgentCoreManager:
    def __init__(self, root=None, config=None):
        self.root = Path(root or Path(__file__).resolve().parents[1]).resolve()
        self.config = config or {}
        self.ruflo_ready = False
        self.skills_registry = {}

    def _path(self, name):
        if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name):
            raise TaskError('اسم المهارة غير صالح. استخدم /skills.')
        path = self.root
        for component in ('ai_tools', 'agent-skills', 'skills', name, 'SKILL.md'):
            path = path / component
            if path.is_symlink():
                raise TaskError('لا يمكن تحميل مهارة عبر رابط رمزي.')
        return path

    def read_skill(self, name):
        path = self._path(name)
        try:
            with path.open('rb') as handle:
                data = handle.read(SKILL_LIMIT + 1)
            if len(data) > SKILL_LIMIT:
                raise TaskError('المهارة أكبر من حد السياق المسموح.')
            content = data.decode('utf-8')
        except (OSError, UnicodeError):
            raise TaskError('تعذر قراءة المهارة: ' + name) from None
        if not content.strip():
            raise TaskError('ملف المهارة فارغ: ' + name)
        return content, hashlib.sha256(data).hexdigest()

    def load_agent_skills(self):
        # Validate ancestors before inspecting directories; never import plugin code.
        base = self._path('probe').parent.parent
        registry = {}
        if base.is_dir():
            for directory in sorted(base.iterdir()):
                if len(registry) >= 100:
                    break
                if directory.is_symlink() or not directory.is_dir():
                    continue
                try:
                    content, digest = self.read_skill(directory.name)
                except TaskError:
                    continue
                registry[directory.name] = {'sha256': digest, 'bytes': len(content.encode('utf-8'))}
        self.skills_registry = registry
        return registry

    def context(self, names):
        if not isinstance(names, list) or len(names) > MAX_SELECTED:
            raise TaskError('اختر مهارتين كحد أقصى.')
        if any(not isinstance(name, str) for name in names) or len(set(names)) != len(names):
            raise TaskError('قائمة المهارات غير صالحة أو مكررة.')
        sections, evidence = [], []
        for name in names:
            content, digest = self.read_skill(name)
            sections.append(f'SKILL {name} SHA256 {digest}\n{content}\nEND SKILL')
            evidence.append({'name': name, 'sha256': digest})
        context = '\n\n'.join(sections)
        if len(context.encode('utf-8')) > CONTEXT_LIMIT:
            raise TaskError('المهارات المختارة تتجاوز حد السياق؛ اختر مهارة واحدة.')
        return context, evidence

    def init_ruflo_workflow(self, workflow_name='main_pipeline'):
        raise TaskError('Ruflo غير مربوط بعد؛ وجود المجلد لا يعني تشغيله.')

    def status(self):
        count = len(self.load_agent_skills())
        return (f'Agent Skills: جسر قراءة متصل؛ {count} مهارة قابلة للتحميل.\n'
                'المهارة إرشادات للنموذج؛ ليست تنفيذ أدوات أو اختبارات.\n'
                'Ruflo / Context7 / Graphify / MCP / Strix: غير مربوطة بعد.')
