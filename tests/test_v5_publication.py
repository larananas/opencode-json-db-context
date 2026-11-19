"""Distribution contract for the public V5 release."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class V5PublicationContracts(unittest.TestCase):
    def test_all_six_os_architectures_are_in_workflow(self):
        workflow = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
        for runner in ('ubuntu-24.04', 'ubuntu-24.04-arm', 'windows-2025',
                       'windows-11-arm', 'macos-15-intel', 'macos-15'):
            self.assertIn(runner, workflow)
        self.assertIn("python: '3.10'", workflow)
        self.assertIn("python: '3.13'", workflow)
        self.assertIn('unittest discover', workflow)
        self.assertIn('check_publication.py', workflow)

    def test_live_integration_is_real_not_mocked(self):
        workflow = (ROOT / '.github/workflows/sql-integration.yml').read_text()
        for image in ('mariadb:11', 'mysql:8.4', 'postgres:17'):
            self.assertIn(image, workflow)
        self.assertIn('test_live_sql.py', workflow)
        fixture = (ROOT / 'tests/test_live_sql.py').read_text()
        self.assertIn('CREATE TABLE context_v5_users', fixture)
        self.assertIn('export_database(spec', fixture)
        self.assertIn("db != 'opencode_ci'", fixture)

    def test_default_scope_and_coherence_are_explicit(self):
        assert (ROOT / 'scripts/mcp_server.py').is_file()
        config = json.loads((ROOT / 'opencode.jsonc').read_text())
        self.assertEqual(config['default_agent'], 'tohru-db')
        self.assertEqual(config['mcp']['servers']['tohru_db']['codemode'], False)
        contents = (ROOT / 'AGENTS.md').read_text()
        self.assertIn('When the user supplies only connection credentials', contents)
        self.assertIn('coherent_samples=true', contents)
        self.assertIn('connect_only=true', contents)
        self.assertIn('DB_DATABASE', contents)

    def test_publication_guard_passes_on_clean_checkout(self):
        result = subprocess.run([sys.executable, 'scripts/check_publication.py'], cwd=ROOT,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_french_readme_and_english_agents(self):
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        self.assertIn('Connexion : identifiants', readme)
        self.assertIn('## 🧪 Tests', readme)
        self.assertIn('Docker', readme)
        self.assertIn('MongoDB', readme)
        for filename in ('AGENTS.md', '.opencode/agents/tohru-db.md',
                         '.opencode/commands/export-db.md', '.opencode/commands/connect-db.md',
                         '.opencode/skills/database-export/SKILL.md'):
            content = (ROOT / filename).read_text()
            self.assertNotIn('échantillons', content)
            self.assertNotIn('Madame', content)


if __name__ == '__main__':
    unittest.main()
