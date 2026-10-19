"""Bounded static checks only: never execute generated code or npm scripts."""
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.parse import unquote, urlsplit

from nova_runtime import TaskError, safe_path, validate_content, strict_json


def check_js(source, label, module=True):
    node = shutil.which('node')
    if not node:
        raise TaskError('Node.js مطلوب لفحص JavaScript؛ لم يُحفظ أي تغيير.')
    env = os.environ.copy()
    env.pop('NODE_OPTIONS', None)
    env.pop('NODE_PATH', None)
    try:
        with tempfile.TemporaryDirectory(prefix='nova-check-') as check_dir:
            result = subprocess.run([node, '--check', '--input-type=' + ('module' if module else 'commonjs')],
                                    input=source, text=True, capture_output=True, timeout=15,
                                    cwd=check_dir, env=env, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise TaskError('تعذر إكمال فحص JavaScript: ' + label) from None
    if result.returncode:
        # Do not echo source code or raw process output into Telegram/logs.
        raise TaskError('خطأ صياغة JavaScript في ' + label + '؛ لم يُحفظ التعديل.')


class HtmlChecks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.scripts = []
        self.links = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script':
            if self.current is not None:
                raise TaskError('تداخل غير صالح لعناصر script.')
            self.current = [attrs, '']
            if attrs.get('src'):
                self.links.append(attrs['src'])
        elif tag == 'link' and 'stylesheet' in attrs.get('rel', '').lower().split() and attrs.get('href'):
            self.links.append(attrs['href'])

    def handle_data(self, data):
        if self.current is not None:
            self.current[1] += data

    def handle_endtag(self, tag):
        if tag == 'script' and self.current is not None:
            self.scripts.append(self.current)
            self.current = None


def validate_bundle(root, changes):
    planned = dict(changes)
    reports = []
    for name, content in changes:
        safe_path(root, name)
        validate_content(name, content)
        suffix = Path(name).suffix.lower()
        if suffix == '.json':
            reports.append(name + ': JSON صالح')
        elif suffix == '.js':
            check_js(content, name)
            reports.append(name + ': فحص صياغة JavaScript ناجح')
        elif suffix == '.html':
            parser = HtmlChecks()
            parser.feed(content)
            parser.close()
            if parser.current is not None:
                raise TaskError('عنصر script غير مغلق في ' + name)
            for index, (attrs, source) in enumerate(parser.scripts):
                if attrs.get('src'):
                    continue
                kind = attrs.get('type', '').lower()
                if kind in ('', 'module', 'text/javascript', 'application/javascript'):
                    check_js(source, f'{name}:script[{index}]', module=kind == 'module')
                elif kind in ('importmap', 'application/ld+json', 'application/json'):
                    try:
                        strict_json(source)
                    except ValueError:
                        raise TaskError('JSON داخل HTML غير صالح: ' + name) from None
            for link in parser.links:
                url = urlsplit(link)
                if url.scheme or url.netloc or not url.path:
                    continue  # No network requests; remote assets are not verified.
                decoded = unquote(url.path)
                candidate = (Path(root) / 'game_engine' / decoded.lstrip('/') if decoded.startswith('/')
                             else Path(root) / Path(name).parent / decoded)
                resolved = candidate.resolve()
                if not resolved.is_relative_to((Path(root) / 'game_engine').resolve()):
                    raise TaskError('مرجع HTML محلي خارج game_engine: ' + name)
                # Reject symlink components even when they resolve within the project.
                for parent in (candidate, *candidate.parents):
                    if parent == Path(root):
                        break
                    if parent.is_symlink():
                        raise TaskError('مرجع HTML يستخدم رابطًا رمزيًا: ' + name)
                relative = resolved.relative_to(Path(root).resolve()).as_posix()
                if relative not in planned and not resolved.is_file():
                    raise TaskError('ملف script أو stylesheet محلي مفقود: ' + relative)
            reports.append(name + ': فحص السكربتات الداخلية ومراجع script/stylesheet المحلية ناجح؛ '
                           'الشكل المرئي والروابط الخارجية لم تُختبر')
        else:
            reports.append(name + ': فحص النص والحجم والأسرار فقط؛ لا يوجد محلل لهذه الصيغة بعد')
    return reports
