"""Report actual local readiness; live probes list tools, never launch agents/scans."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ai_tools.agent_core import AgentCoreManager
from nova_tools import invoke_tool


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / '.env')
    except ImportError:
        pass
    report = {'skills_readable': len(AgentCoreManager(ROOT).load_agent_skills()), 'tools': {}}
    for name, relative in [('opencrabs', '.nova/tools/opencrabs'), ('bridge', '.nova/tools/bridge/bin/python'),
                           ('ruflo', '.nova/tools/node/node_modules/.bin/ruflo'),
                           ('graphify', '.nova/tools/graphify/bin/graphify'), ('strix', '.nova/tools/strix/bin/strix')]:
        report['tools'][name] = {'installed': (ROOT / relative).is_file()}
    for name in ('CONTEXT7_API_KEY', 'API_KEY_21ST', 'GEMINI_API_KEY', 'STRIX_LLM', 'LLM_API_KEY'):
        report[name] = 'set' if os.getenv(name) else 'not set'
    if args.live:
        for name in ('ruflo', 'context7', 'graphify', '21st'):
            try:
                result = json.loads(invoke_tool(ROOT, {'server': name, 'action': 'list'}))
                report['tools'].setdefault(name, {}).update(connected=True, tool_count=result.get('total_tools'))
            except Exception:
                report['tools'].setdefault(name, {}).update(connected=False, note='Check key, installation, graph and network.')
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print('Connected means MCP handshake/list succeeded, not that a coding task or security scan passed.')


if __name__ == '__main__':
    main()
