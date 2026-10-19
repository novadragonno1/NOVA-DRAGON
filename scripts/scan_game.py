"""Explicit local-game Strix entry point; never runs during setup or chat."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import shutil

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.argv[1:] != ['--run']:
        print('Explicit game-only scan: .venv/bin/python scripts/scan_game.py --run')
        print('Requires running Docker, STRIX_LLM and LLM_API_KEY. Model API usage consumes quota.')
        return
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    if not os.getenv('STRIX_LLM') or not os.getenv('LLM_API_KEY'):
        raise SystemExit('Set STRIX_LLM and LLM_API_KEY privately in .env.')
    subprocess.run(['docker', 'info'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    work = ROOT / '.nova/strix'
    work.mkdir(parents=True, exist_ok=True)
    source = ROOT / 'game_engine'
    if source.is_symlink() or any(p.is_symlink() for p in source.rglob('*')):
        raise SystemExit('Game scan rejects symlinks.')
    with tempfile.TemporaryDirectory(dir=work) as tmp:
        target = Path(tmp) / 'game'
        shutil.copytree(source, target, ignore=shutil.ignore_patterns('.env*', '.git', 'node_modules'))
        env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'DOCKER_HOST', 'STRIX_LLM', 'LLM_API_KEY')}
        result = subprocess.run([str(ROOT / '.nova/tools/strix/bin/strix'), '-n', '--target', str(target),
                                 '--scan-mode', 'quick', '--max-turns', '20'],
                                cwd=work, env=env)
    print('Strix exit code:', result.returncode, '; inspect reports in .nova/strix/strix_runs.')
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
