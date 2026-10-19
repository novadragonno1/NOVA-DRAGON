"""Separate game profile; never imports trading config, keys, history or Telegram."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nova_runtime import atomic_write


def prepare(root=ROOT, profile=None):
    profile = profile or Path.home() / '.opencrabs/profiles/nova-game'
    if profile.is_symlink():
        raise RuntimeError('Game profile must not be a symlink.')
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    model = os.getenv('GEMINI_MODEL', 'models/gemini-3.8-flash').removeprefix('models/')
    config = ('[providers.gemini]\nenabled = true\ndefault_model = ' + json.dumps(model) +
              '\n[agent]\napproval_policy = "ask"\nauto_update = false\nrsi_enabled = false\n'
              '[channels.telegram]\nenabled = false\n')
    command = (shlex.quote(str(root / '.nova/tools/bridge/bin/python')) + ' ' +
               shlex.quote(str(root / 'scripts/tool_bridge.py')) + ' --params-file "$OPENCRABS_PARAMS"')
    tools = ('[[tools]]\nname = "nova_tools"\n'
             'description = "Use the NOVA gateway. First list a server: ruflo, context7, graphify, 21st, skills. '
             'Then call only enabled tools using their returned schemas. No shell arguments in request."\n'
             'executor = "shell"\ncommand = ' + json.dumps(command) +
             '\ntimeout_secs = 110\nrequires_approval = false\nenabled = true\n'
             '[[tools.params]]\nname = "request"\ntype = "string"\nrequired = true\n'
             'description = \'JSON object: {"server":"skills","action":"list"} or '
             '{"server":"skills","action":"call","tool":"read","arguments":{"name":"planning-and-task-breakdown"}}\'\n')
    guidance = ('# NOVA game workspace\nWork only in ' + str(root) + '. This is the game, not the trading project.\n'
                'Use nova_tools to discover and read relevant Agent Skills automatically before a task. '
                'Use Context7 for library docs, Graphify for indexed relationships, Ruflo for available coordination/memory tools. '
                'Read the live tool schemas. Missing keys, unavailable tools and failures must be reported honestly.\n'
                'Do not claim that retrieving a tool list means a coding workflow executed. '
                'Do not publish, merge, send Telegram messages, enable channels, or start Strix scans automatically.\n'
                'NOVA Telegram remains the primary writer. Produce proposals for it; do not independently edit its working files. '
                'This is an instruction boundary, not an OS sandbox. Never load .env or other credential files into model context.\n')
    for name, text in [('config.toml', config), ('tools.toml', tools), ('AGENTS.md', guidance)]:
        target = profile / name
        if target.is_symlink():
            raise RuntimeError('Profile files must not be symlinks.')
        if target.exists() and target.read_text() != text:
            # Preserve owner customization; emit a reviewable proposed file.
            target = profile / (name + '.nova-proposed')
        atomic_write(target, text)
        target.chmod(0o600)
    return profile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'chat', 'run', 'status'])
    parser.add_argument('prompt', nargs='?')
    args = parser.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / '.env')
    except ImportError:
        pass
    binary = ROOT / '.nova/tools/opencrabs'
    expected = json.loads((ROOT / 'integrations/opencrabs-source.json').read_text())['binary_sha256']
    if not binary.is_file() or hashlib.sha256(binary.read_bytes()).hexdigest() != expected:
        raise SystemExit('Install the verified binary: python scripts/setup_tools.py --only opencrabs')
    profile = prepare()
    if args.action == 'prepare':
        print('Game profile prepared: ' + str(profile))
        print('Existing customizations are preserved; inspect any .nova-proposed files.')
        return
    if args.action in ('chat', 'run') and not os.getenv('GEMINI_API_KEY'):
        raise SystemExit('GEMINI_API_KEY is required in the environment or .env; use the project .venv Python.')
    command = [str(binary), '--profile', 'nova-game', args.action]
    if args.action == 'run':
        if not args.prompt:
            raise SystemExit('A task prompt is required.')
        command.append(args.prompt)
    # No --auto-approve, no copied Telegram token. The dedicated profile owns its history.
    env = {k: v for k, v in os.environ.items() if not any(w in k.upper() for w in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY'))}
    for key in ('GEMINI_API_KEY', 'CONTEXT7_API_KEY', 'API_KEY_21ST'):
        if os.getenv(key):
            env[key] = os.environ[key]
    raise SystemExit(subprocess.run(command, cwd=ROOT, env=env).returncode)


if __name__ == '__main__':
    main()
