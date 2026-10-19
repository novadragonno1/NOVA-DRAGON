"""Linux lifecycle commands for this workspace's NOVA process only."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PROC_ROOT = Path('/proc')


def python_for(root):
    candidate = Path(root) / '.venv/bin/python'
    return str(candidate) if candidate.is_file() else sys.executable


def bot_pids(root):
    root = Path(root).resolve()
    found = []
    for proc in PROC_ROOT.iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            args = (proc / 'cmdline').read_bytes().decode().split('\0')
            if not args or not Path(args[0]).name.startswith('python'):
                continue
            rest = args[1:]
            while rest and rest[0] in ('-u', '-B', '-E', '-s', '-S', '-I'):
                rest = rest[1:]
            if not rest or rest[0].startswith('-'):
                continue
            if (proc / 'cwd').resolve() != root or (root / rest[0]).resolve() != root / 'bot.py':
                continue
            found.append(int(proc.name))
        except (OSError, UnicodeError):
            continue
    return found


def stop(root):
    for pid in bot_pids(root):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and bot_pids(root):
        time.sleep(0.1)
    if bot_pids(root):
        raise RuntimeError('Old bot did not exit; no new bot was started.')


def configured(root):
    check = ('import os; from dotenv import load_dotenv; load_dotenv(".env"); '
             'owner=os.getenv("TELEGRAM_OWNER_ID", "").strip(); '
             'ok=bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("GEMINI_API_KEY") '
             'and owner.isdigit() and int(owner)>0); raise SystemExit(0 if ok else 1)')
    result = subprocess.run([python_for(root), '-c', check], cwd=root,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    return result.returncode == 0


def start(root):
    if bot_pids(root):
        print('NOVA is already running; no duplicate process started.')
        return
    if (root / '.nova/restore-in-progress').exists():
        raise RuntimeError('State restore is incomplete. Finish restore before starting NOVA.')
    if not configured(root):
        raise RuntimeError('Run setup_env.py and configure .env / Codespaces secrets first.')
    state = root / '.nova'
    state.mkdir(mode=0o700, exist_ok=True)
    with (state / 'bot.log').open('ab') as log:
        process = subprocess.Popen([python_for(root), '-u', str(root / 'bot.py')], cwd=root,
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True)
    time.sleep(3)
    if process.poll() is not None:
        raise RuntimeError('Bot exited. Inspect .nova/bot.log privately.')
    print(f'NOVA process started: {process.pid}.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', nargs='?', default='restart', choices=['start', 'stop', 'restart', 'status', 'autostart'])
    action = parser.parse_args().action
    try:
        if action == 'status':
            pids = bot_pids(ROOT)
            print('Running PIDs: ' + ', '.join(map(str, pids)) if pids else 'NOVA is stopped.')
        elif action == 'stop':
            stop(ROOT)
            print('NOVA is stopped.')
        elif action == 'autostart':
            if configured(ROOT):
                start(ROOT)
            else:
                print('Autostart skipped: configure credentials and TELEGRAM_OWNER_ID first.')
        else:
            if action == 'restart':
                if not configured(ROOT):
                    raise RuntimeError('Configuration/dependencies missing; existing bot was not stopped.')
                stop(ROOT)
            start(ROOT)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        raise SystemExit(str(error)) from None


if __name__ == '__main__':
    main()
