"""Restart only Python bot.py processes belonging to this Linux workspace."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def main():
    root = Path(__file__).resolve().parents[1]
    stopped = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            args = (proc / 'cmdline').read_bytes().decode().split('\0')
            if not args or not Path(args[0]).name.startswith('python'):
                continue
            cwd = (proc / 'cwd').resolve()
            if cwd != root:
                continue
            if not any((cwd / arg).resolve() == root / 'bot.py' for arg in args[1:] if arg):
                continue
            os.kill(int(proc.name), signal.SIGTERM)
            stopped.append(int(proc.name))
        except (OSError, UnicodeError):
            continue
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(Path(f'/proc/{pid}').exists() for pid in stopped):
        time.sleep(0.1)
    if any(Path(f'/proc/{pid}').exists() for pid in stopped):
        raise SystemExit('An old bot has not exited. No new process was started.')
    state = root / '.nova'
    state.mkdir(mode=0o700, exist_ok=True)
    with (state / 'bot.log').open('ab') as log:
        process = subprocess.Popen([sys.executable, '-u', str(root / 'bot.py')], cwd=root,
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True)
    time.sleep(3)
    if process.poll() is not None:
        raise SystemExit('Bot exited. Check .nova/bot.log locally; do not publish logs or keys.')
    print(f'Bot process started: {process.pid}. Send /status in Telegram to verify.')


if __name__ == '__main__':
    main()
