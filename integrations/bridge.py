"""One bounded tool gateway shared by Telegram and OpenCrabs."""
import asyncio
from contextlib import AsyncExitStack
import json
import os
import re
from pathlib import Path

from ai_tools.agent_core import AgentCoreManager
from nova_runtime import TaskError

ROOT = Path(__file__).resolve().parents[1]


def search_name(name):
    return isinstance(name, str) and bool(re.search(r'(^|_)(search|find|lookup)(_|$)', name))


def enabled_tool(config, tool):
    if tool.name in config['allowed_tools']:
        return True
    annotations = tool.annotations
    return bool(config.get('read_only_search') and search_name(tool.name) and annotations
                and annotations.read_only_hint is True and annotations.destructive_hint is not True)


def manifest(root=ROOT):
    return json.loads((root / 'integrations/toolchain.json').read_text())


def check_request(request, root=ROOT):
    if not isinstance(request, dict) or set(request) - {'server', 'action', 'tool', 'arguments'}:
        raise TaskError('Invalid tool request.')
    server, action = request.get('server'), request.get('action', 'list')
    if server != 'skills' and server not in manifest(root)['servers']:
        raise TaskError('Unknown server.')
    if action not in ('list', 'call'):
        raise TaskError('Action must be list or call.')
    args = request.get('arguments', {})
    if not isinstance(args, dict) or len(json.dumps(args).encode()) > 16_000:
        raise TaskError('Tool arguments must be an object up to 16KB.')
    if action == 'call':
        allowed = ['read'] if server == 'skills' else manifest(root)['servers'][server]['allowed_tools']
        discovery_search = server == '21st' and search_name(request.get('tool'))
        if request.get('tool') not in allowed and not discovery_search:
            raise TaskError('Tool is not enabled in the reviewed allowlist.')
        if server == 'graphify' and 'project_path' in args:
            raise TaskError('Graphify is restricted to the prepared game graph.')
        if server == 'graphify' and any(type(args.get(k, 1)) is not int or not 1 <= args.get(k, 1) <= limit
                                      for k, limit in [('token_budget', 4000), ('depth', 6), ('max_hops', 12), ('top_n', 50)]):
            raise TaskError('Graph query exceeds its budget.')
    return server, action, args


async def invoke(request, root=ROOT):
    server, action, args = check_request(request, root)
    if server == 'skills':
        core = AgentCoreManager(root)
        if action == 'list':
            return {'skills': core.load_agent_skills()}
        content, evidence = core.context([args.get('name')])
        return {'content': content, 'evidence': evidence}
    from mcp import Client, StdioServerParameters
    from mcp.client.streamable_http import streamable_http_client
    import httpx2

    config = manifest(root)['servers'][server]
    async with AsyncExitStack() as stack:
        if config['transport'] == 'stdio':
            command = root / config['command']
            if not command.is_file():
                raise TaskError('Tool is not installed. Run scripts/setup_tools.py.')
            # Do not hand model/Telegram credentials to local MCP servers.
            env = {key: value for key, value in os.environ.items()
                   if key in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'SYSTEMROOT')}
            cwd = root / '.nova/tool-work' / server
            cwd.mkdir(parents=True, exist_ok=True)
            source = StdioServerParameters(command=str(command), args=[a.replace('{root}', str(root)) for a in config['args']], env=env, cwd=str(cwd))
        else:
            key = os.getenv(config['key_env'], '')
            if not key and not config.get('optional_key'):
                raise TaskError('Missing setting: ' + config['key_env'])
            headers = {config['header']: config['prefix'] + key} if key else {}
            http = await stack.enter_async_context(httpx2.AsyncClient(headers=headers, timeout=40))
            source = streamable_http_client(config['url'], http_client=http)
        client = await stack.enter_async_context(Client(source, read_timeout_seconds=45))
        tools = []
        cursor = None
        for _ in range(10):
            page = await client.list_tools(cursor=cursor)
            tools.extend(page.tools)
            cursor = page.next_cursor
            if not cursor:
                break
        else:
            raise TaskError('Tool catalog exceeds pagination limit.')
        if action == 'list':
            return {'server': server, 'connected': True, 'tools': [
                {'name': t.name, 'description': t.description, 'inputSchema': t.input_schema,
                 'enabled': enabled_tool(config, t)} for t in tools]}
        tool = next((t for t in tools if t.name == request['tool']), None)
        if tool is None:
            raise TaskError('The installed server does not expose this tool.')
        if not enabled_tool(config, tool):
            raise TaskError('The discovered tool is not enabled; paid generation/publishing is excluded.')
        import jsonschema
        jsonschema.validate(args, tool.input_schema)
        result = await client.call_tool(tool.name, args)
        return result.model_dump(by_alias=True, mode='json')


def run(request, root=ROOT):
    async def bounded():
        async with asyncio.timeout(100):
            return await invoke(request, root)
    return asyncio.run(bounded())
