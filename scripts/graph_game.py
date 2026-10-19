"""Index only the game's text code, on a disposable copy, without model calls."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nova_runtime import safe_path
from nova_validation import HtmlChecks


def main():
    tool = ROOT / '.nova/tools/graphify/bin/graphify'
    output = ROOT / '.nova/graph'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output, prefix='source-') as tmp:
        staging = Path(tmp)
        total = 0
        count = 0
        mapping = {}
        game = ROOT / 'game_engine'
        if game.is_symlink():
            raise SystemExit('Game directory must not be a symlink.')
        for path in game.rglob('*'):
            if path.suffix not in ('.js', '.ts', '.html', '.css', '.json', '.glsl', '.wgsl') or not path.is_file():
                continue
            source = safe_path(ROOT, path.relative_to(ROOT).as_posix())
            data = source.read_bytes()
            total += len(data)
            count += 1
            if total > 20_000_000 or len(data) > 500_000 or count > 1000:
                raise SystemExit('Game corpus exceeds indexing budget.')
            target = staging / path.relative_to(game)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            mapping[str(target)] = path.relative_to(ROOT).as_posix()
            if path.suffix == '.html':
                parser = HtmlChecks()
                parser.feed(data.decode('utf-8'))
                parser.close()
                for index, (attrs, script) in enumerate(parser.scripts):
                    if attrs.get('src') or attrs.get('type', '').lower() not in ('', 'module', 'text/javascript', 'application/javascript'):
                        continue
                    extracted = target.with_name('__nova_inline_' + target.name + f'_{index}.js')
                    if (path.parent / extracted.name).exists():
                        raise SystemExit('Inline script staging name conflicts with a source file.')
                    extracted.write_text(script)
                    mapping[str(extracted)] = path.relative_to(ROOT).as_posix() + f':script[{index}]'
        env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'LANG')}
        subprocess.run([str(tool), 'extract', str(staging), '--code-only', '--no-cluster',
                        '--force', '--max-workers', '1', '--out', str(output)], env=env, check=True, timeout=180)
        (output / 'source-map.json').write_text(json.dumps(mapping, ensure_ascii=False, indent=2))
    print('Local code graph saved under .nova/graph. Rebuild after code changes.')


if __name__ == '__main__':
    main()
