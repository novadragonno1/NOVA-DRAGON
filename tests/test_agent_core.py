from pathlib import Path
import tempfile
import unittest

from ai_tools.agent_core import AgentCoreManager, SKILL_LIMIT
from nova_runtime import TaskError


class SkillBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.core = AgentCoreManager(self.root)
        self.base = self.root / 'ai_tools/agent-skills/skills'
        self.path = self.base / 'review/SKILL.md'
        self.path.parent.mkdir(parents=True)
        self.path.write_text('Review correctness. Never invent results.')

    def test_catalog_and_context_include_actual_content_and_digest(self):
        catalog = self.core.load_agent_skills()
        context, evidence = self.core.context(['review'])
        self.assertIn(self.path.read_text(), context)
        self.assertEqual(evidence[0]['sha256'], catalog['review']['sha256'])
        self.path.write_text('Changed guidance')
        self.assertNotEqual(self.core.context(['review'])[1], evidence)

    def test_paths_and_duplicates_rejected(self):
        for names in [['../secrets'], ['/etc/passwd'], ['review', 'review'], ['a','b','c'], 'review']:
            with self.subTest(names=names), self.assertRaises(TaskError):
                self.core.context(names)

    def test_symlink_file_and_parent_rejected(self):
        outside = self.root / 'secret'
        outside.write_text('secret')
        self.path.unlink()
        self.path.symlink_to(outside)
        with self.assertRaises(TaskError):
            self.core.context(['review'])
        self.path.unlink()
        self.path.parent.rmdir()
        self.path.parent.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(TaskError):
            self.core.context(['review'])

    def test_oversized_and_invalid_utf8_fail_without_partial_load(self):
        for data in [b'x' * (SKILL_LIMIT + 1), b'\xff', b'']:
            self.path.write_bytes(data)
            with self.assertRaises(TaskError):
                self.core.context(['review'])
            self.assertEqual(self.core.load_agent_skills(), {})

    def test_ruflo_is_not_reported_ready(self):
        with self.assertRaises(TaskError):
            self.core.init_ruflo_workflow()
        self.assertFalse(self.core.ruflo_ready)

    def test_combined_context_limit(self):
        self.path.write_text('x' * 40_000)
        other = self.base / 'plan/SKILL.md'
        other.parent.mkdir()
        other.write_text('y' * 40_000)
        with self.assertRaises(TaskError):
            self.core.context(['review', 'plan'])
