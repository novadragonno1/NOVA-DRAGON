"""Create project-local Python environment. No credentials or bot startup here."""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.version_info < (3, 12):
        raise SystemExit('Python 3.12+ is required; use the devcontainer configuration.')
    if not shutil.which('node') or not shutil.which('git'):
        raise SystemExit('Node.js and Git are required; rebuild with the devcontainer configuration.')
    subprocess.run(['node', '--version'], check=True)
    python = ROOT / '.venv/bin/python'
    if not python.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(ROOT / '.venv')], check=True)
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.txt')], check=True, cwd=ROOT)
    print('Environment ready. .env and saved agent state were not changed.')


if __name__ == '__main__':
    main()
