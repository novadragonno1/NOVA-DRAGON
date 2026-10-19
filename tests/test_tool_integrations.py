import asyncio
import json
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock

from integrations.bridge import check_request, invoke, enabled_tool, ROOT
from nova_runtime import TaskError
from nova_tools import invoke_tool
from scripts.opencrabs_game import prepare
from scripts.setup_tools import download_opencrabs


class ToolIntegrationTests(unittest.TestCase):
    def test_21st_only_discovered_read_only_search_is_enabled(self):
        config = {'allowed_tools': [], 'read_only_search': True}
        tool = SimpleNamespace(name='search_components', annotations=None)
        self.assertFalse(enabled_tool(config, tool))
        tool.annotations = SimpleNamespace(read_only_hint=True, destructive_hint=False)
        self.assertTrue(enabled_tool(config, tool))
        tool.name = 'generate_ui'
        self.assertFalse(enabled_tool(config, tool))

    def test_unreviewed_calls_and_servers_rejected(self):
        for request in [
            {'server': '../../secret'},
            {'server': 'ruflo', 'action': 'call', 'tool': 'terminal_execute'},
            {'server': '21st', 'action': 'call', 'tool': 'publish'},
            {'server': 'ruflo', 'action': 'call', 'tool': 'memory_store', 'arguments': []},
            {'server': 'graphify', 'action': 'call', 'tool': 'graph_stats', 'arguments': {'project_path': '/private'}},
            {'server': 'graphify', 'action': 'call', 'tool': 'query_graph', 'arguments': {'token_budget': 999999}},
        ]:
            with self.subTest(request=request), self.assertRaises(TaskError):
                check_request(request)

    def test_valid_calls_preserve_parameters_as_data(self):
        args = {'key': 'x', 'value': '$(touch unwanted); `false`'}
        self.assertEqual(check_request({'server': 'ruflo', 'action': 'call', 'tool': 'memory_store', 'arguments': args})[2], args)

    def test_skill_bridge_reads_without_mcp_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / 'ai_tools/agent-skills/skills/plan/SKILL.md'
            skill.parent.mkdir(parents=True)
            skill.write_text('Plan using evidence')
            result = asyncio.run(invoke({'server': 'skills', 'action': 'call', 'tool': 'read', 'arguments': {'name':'plan'}}, root))
            self.assertIn('Plan using evidence', result['content'])

    def test_profile_configuration_and_shell_quoting(self):
        with tempfile.TemporaryDirectory(prefix='nova space ') as tmp:
            root = Path(tmp)
            profile = root / 'profile'
            with patch.dict(os.environ, {'GEMINI_MODEL':'models/test-model'}):
                prepare(root, profile)
            config = tomllib.loads((profile / 'config.toml').read_text())
            self.assertFalse(config['channels']['telegram']['enabled'])
            self.assertFalse(config['agent']['auto_update'])
            self.assertEqual(config['providers']['gemini']['default_model'], 'test-model')
            tools = tomllib.loads((profile / 'tools.toml').read_text())['tools']
            self.assertIn('"$OPENCRABS_PARAMS"', tools[0]['command'])
            self.assertNotIn('{{request}}', tools[0]['command'])
            self.assertNotIn('TOKEN', (profile / 'config.toml').read_text())

    def test_profile_preserves_customizations(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / 'profile'
            prepare(Path(tmp), profile)
            config = profile / 'config.toml'
            config.write_text('owner customization')
            prepare(Path(tmp), profile)
            self.assertEqual(config.read_text(), 'owner customization')
            self.assertTrue((profile / 'config.toml.nova-proposed').exists())

    def test_checksum_mismatch_does_not_install_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'integrations').mkdir()
            (root / 'integrations/opencrabs-source.json').write_text((ROOT / 'integrations/opencrabs-source.json').read_text())
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.side_effect = [b'not the archive', b'']
            with patch('scripts.setup_tools.urllib.request.urlopen', return_value=response), self.assertRaisesRegex(RuntimeError,'checksum'):
                download_opencrabs(root)
            self.assertFalse((root / '.nova/tools/opencrabs').exists())

    def test_tool_error_does_not_write_success_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / '.nova/tools/bridge/bin/python'
            executable.parent.mkdir(parents=True)
            executable.touch()
            response = Mock(returncode=0, stdout=json.dumps({'isError':True,'error':'failed'}))
            with patch('nova_tools.subprocess.run',return_value=response), self.assertRaises(TaskError):
                invoke_tool(root, {'server':'ruflo','action':'list'})
            self.assertFalse((root / '.nova/tool-results/ruflo.json').exists())

    def test_tool_receipt_and_catalog_only_expose_enabled_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / '.nova/tools/bridge/bin/python'
            executable.parent.mkdir(parents=True)
            executable.touch()
            response = Mock(returncode=0, stdout=json.dumps({'tools':[{'name':'a','enabled':True},{'name':'b','enabled':False}]}))
            with patch('nova_tools.subprocess.run', return_value=response):
                result = json.loads(invoke_tool(root, {'server':'ruflo','action':'list'}))
            self.assertEqual(result['total_tools'],2)
            self.assertEqual([t['name'] for t in result['tools']],['a'])
            self.assertTrue((root / '.nova/tool-results/ruflo.json').exists())
