"""Install pinned tools locally; never starts Telegram, a model call, or a scan."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def download_opencrabs(root=ROOT):
    info = json.loads((root / 'integrations/opencrabs-source.json').read_text())
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'AMD64'):
        raise RuntimeError('This imported OpenCrabs binary requires Linux x86_64.')
    target = root / '.nova/tools/opencrabs'
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == info['binary_sha256']:
        return target
    archive = target.with_suffix('.zip.part')
    try:
        digest = hashlib.sha256()
        total = 0
        with urllib.request.urlopen(info['url'], timeout=45) as response, archive.open('wb') as out:
            while data := response.read(1024 * 1024):
                total += len(data)
                if total > 50_000_000:
                    raise RuntimeError('Unexpected OpenCrabs archive size.')
                digest.update(data)
                out.write(data)
        if digest.hexdigest() != info['archive_sha256']:
            raise RuntimeError('OpenCrabs archive checksum mismatch.')
        with zipfile.ZipFile(archive) as source:
            if source.namelist() != ['opencrabs'] or source.getinfo('opencrabs').file_size > 120_000_000:
                raise RuntimeError('Unexpected archive contents.')
            data = source.read('opencrabs')
        if hashlib.sha256(data).hexdigest() != info['binary_sha256']:
            raise RuntimeError('OpenCrabs binary checksum mismatch.')
        staged = target.with_suffix('.part')
        staged.write_bytes(data)
        staged.chmod(0o700)
        staged.replace(target)
    finally:
        archive.unlink(missing_ok=True)
    return target


def install_python(name, package, root=ROOT):
    directory = root / '.nova/tools' / name
    python = directory / 'bin/python'
    if not python.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(directory)], check=True)
    subprocess.run([str(python), '-m', 'pip', 'install', package] + (['socksio==1.0.0'] if name == 'bridge' else []), check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', choices=['opencrabs', 'bridge', 'ruflo', 'graphify', 'strix'])
    args = parser.parse_args()
    packages = json.loads((ROOT / 'integrations/toolchain.json').read_text())['packages']
    actions = {
        'opencrabs': lambda: download_opencrabs(),
        'bridge': lambda: install_python('bridge', 'mcp==' + packages['mcp']),
        'ruflo': lambda: subprocess.run(['npm', 'install', '--prefix', str(ROOT / '.nova/tools/node'),
                    '--save-exact', '--no-audit', '--no-fund', 'ruflo@' + packages['ruflo']], check=True),
        'graphify': lambda: install_python('graphify', 'graphifyy[mcp]==' + packages['graphifyy']),
        'strix': lambda: install_python('strix', 'strix-agent==' + packages['strix-agent']),
    }
    failed = []
    for name in ([args.only] if args.only else actions):
        print('Installing ' + name, flush=True)
        try:
            actions[name]()
        except Exception:
            failed.append(name)
            print('Installation failed: ' + name + '. Retry with --only ' + name, flush=True)
    print('Context7 and 21st use remote MCP; no additional server install.')
    print('Installation is not a live service check. Run scripts/tools_doctor.py next.')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
