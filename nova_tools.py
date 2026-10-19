"""Invoke the optional isolated tool runtime without importing its dependencies."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from nova_runtime import TaskError, atomic_write


def invoke_tool(root, request):
    root = Path(root)
    python = root / '.nova/tools/bridge/bin/python'
    if not python.is_file():
        raise TaskError('جهز الأدوات أولًا: python scripts/setup_tools.py')
    process = subprocess.run([str(python), str(root / 'scripts/tool_bridge.py'),
                              '--request', json.dumps(request)], cwd=root,
                             capture_output=True, text=True, timeout=115)
    try:
        result = json.loads(process.stdout)
    except ValueError:
        raise TaskError('لم تصل نتيجة صالحة من جسر الأدوات.') from None
    if process.returncode or result.get('isError') or result.get('is_error'):
        raise TaskError('تعذر تنفيذ الأداة: ' + result.get('error', 'راجع إعدادها وحدودها.'))
    if 'tools' in result:
        result = {**result, 'total_tools': len(result['tools']),
                  'tools': [t for t in result['tools'] if t['enabled']]}
    record = {'time': datetime.now(timezone.utc).isoformat(), 'request': request, 'result': result}
    state = root / '.nova/tool-results'
    state.mkdir(parents=True, exist_ok=True)
    atomic_write(state / (request['server'] + '.json'), json.dumps(record, ensure_ascii=False))
    text = json.dumps(result, ensure_ascii=False)
    return text[:16_000] + ('\n[نتيجة مختصرة]' if len(text) > 16_000 else '')


def status(root):
    root = Path(root)
    lines = ['الجسر مُعدّ؛ التثبيت وحده لا يثبت الاتصال. /tool اسم list لاختبار MCP.']
    for name in ('ruflo', 'context7', '21st', 'graphify'):
        path = root / '.nova/tool-results' / (name + '.json')
        try:
            receipt = json.loads(path.read_text())
            lines.append(name + ': آخر استدعاء ناجح ' + receipt['time'])
        except (OSError, ValueError, KeyError):
            lines.append(name + ': لم يُسجل استدعاء ناجح في هذه البيئة بعد.')
    lines.append('Strix: أمر مستقل python scripts/scan_game.py؛ يحتاج Docker ومفتاح نموذج.')
    return '\n'.join(lines)
