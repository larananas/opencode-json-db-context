"""Cross-platform setup, safe permissions and repository publication contracts."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('db_public_setup_test', ROOT / 'scripts' / 'setup.py')
SETUP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SETUP)
sys.path.insert(0, str(ROOT / 'scripts'))
from db_engine import ConnectionSpec, open_connection


class PublicationTests(unittest.TestCase):
    def test_standard_entrypoints_exist(self):
        self.assertTrue((ROOT / 'setup.sh').is_file())
        self.assertTrue((ROOT / 'setup.ps1').is_file())
        self.assertTrue((ROOT / 'README.md').is_file())
        self.assertTrue((ROOT / 'LICENSE').is_file())

    def test_project_mcp_is_disabled_until_setup(self):
        config = json.loads((ROOT / 'opencode.jsonc').read_text())
        self.assertEqual(config['default_agent'], 'tohru-db')
        self.assertTrue(config['mcp']['servers']['tohru_db']['disabled'])
        self.assertEqual(config['mcp']['servers']['tohru_db']['protocol'], 'legacy')

    def test_normal_tools_allowed_and_shell_external_denied(self):
        config = json.loads((ROOT / 'opencode.jsonc').read_text())
        rules = config['permissions']
        self.assertIn({'action':'tohru_db_*', 'resource':'*', 'effect':'allow'}, rules)
        for action in ('shell', 'edit', 'external_directory', 'subagent'):
            self.assertIn({'action':action, 'resource':'*', 'effect':'deny'}, rules)

    def test_gitignore_covers_generated_config_exports_and_secrets(self):
        text = (ROOT / '.gitignore').read_text()
        for pattern in ('export/', '.venv/', '.local/', '.opencode/opencode.jsonc',
                        '.env', '*.sqlite', '*.db', '*.pem', '*.p12'):
            self.assertIn(pattern, text)

    def test_configuration_is_generated_in_project_only(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            venv = project / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
            venv.parent.mkdir(parents=True)
            venv.touch()
            script = project / 'scripts' / 'mcp_server.py'
            script.parent.mkdir(parents=True)
            script.touch()
            with patch.object(SETUP, 'ROOT', project), patch.object(SETUP, 'ENV', project / '.venv'), \
                    patch.object(SETUP, 'LOCAL_CONFIG', project / '.opencode/opencode.jsonc'):
                SETUP.configure()
            cfg = json.loads((project / '.opencode/opencode.jsonc').read_text())
            server = cfg['mcp']['servers']['tohru_db']
            self.assertFalse(server['disabled'])
            self.assertEqual(server['command'], [str(venv.resolve()), str(script.resolve())])
            self.assertEqual(server['cwd'], str(project.resolve()))

    def test_missing_dependency_diagnostic_lists_named_drivers(self):
        with patch.object(SETUP, 'valid_python', return_value=False):
            missing = SETUP.missing_modules()
        self.assertIn('MCP SDK', missing)
        self.assertIn('Oracle driver', missing)
        self.assertIn('SQL Server driver', missing)

    def test_package_approval_defaults_to_no(self):
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value='no'):
            self.assertFalse(SETUP.confirm('Install?'))
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value='yes'):
            self.assertTrue(SETUP.confirm('Install?'))
        self.assertTrue(SETUP.confirm('Install?', yes=True))

    def test_no_internet_install_without_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            project_env = project / '.venv'
            project_env.mkdir()
            with patch.object(SETUP, 'ROOT', project), patch.object(SETUP, 'ENV', project_env), \
                    patch.object(SETUP, 'REQ', ROOT / 'requirements.txt'), \
                    patch.object(SETUP, 'valid_python', return_value=True), \
                    patch.object(SETUP, 'installed', return_value=False), \
                    patch.object(SETUP, 'confirm', return_value=False), \
                    patch.object(SETUP, 'run') as runner:
                with self.assertRaisesRegex(RuntimeError, 'declined'):
                    SETUP.setup()
                runner.assert_not_called()

    def test_sqlite_external_file_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / 'outside.sqlite'
            sqlite3.connect(database).close()
            with self.assertRaisesRegex(ValueError, 'inside the project'):
                open_connection(ConnectionSpec(engine='sqlite', path=str(database)))

    def test_sqlite_symlink_outside_is_refused(self):
        (ROOT / '.local').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory(dir=ROOT / '.local') as local:
            database = Path(temp) / 'outside.sqlite'
            sqlite3.connect(database).close()
            symlink = Path(local) / 'link.sqlite'
            try:
                symlink.symlink_to(database)
            except OSError:
                self.skipTest('Symlink creation is unavailable on this platform')
            with self.assertRaisesRegex(ValueError, 'inside the project'):
                open_connection(ConnectionSpec(engine='sqlite', path=str(symlink)))

    def test_sqlite_inside_project_remains_readonly(self):
        (ROOT / '.local').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / '.local') as tmp:
            db = Path(tmp) / 'safe.sqlite'
            conn = sqlite3.connect(db)
            conn.execute('create table t(id integer)')
            conn.close()
            readonly = open_connection(ConnectionSpec(engine='sqlite', path=str(db)))
            with self.assertRaises(sqlite3.OperationalError):
                readonly.execute('insert into t values (1)')
            readonly.close()

    def test_all_project_agent_instructions_are_english(self):
        sources = [ROOT / 'AGENTS.md', ROOT / '.opencode/agents/tohru-db.md',
                   ROOT / '.opencode/commands/export-db.md', ROOT / '.opencode/commands/connect-db.md',
                   ROOT / '.opencode/skills/database-export/SKILL.md']
        for source in sources:
            body = source.read_text(encoding='utf-8')
            self.assertNotIn('Madame', body)
            self.assertNotIn('parlez français', body.lower())
            self.assertNotIn('Speak French', body)
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        self.assertIn('Installation (Windows, Linux et macOS)', readme)

    def test_posix_check_is_diagnostic_only(self):
        if sys.platform == 'win32':
            self.skipTest('POSIX-only command')
        local = ROOT / '.opencode/opencode.jsonc'
        existed = local.exists()
        result = subprocess.run(['sh', 'setup.sh', '--check'], cwd=str(ROOT),
                                capture_output=True, text=True, timeout=30)
        # Missing optional drivers is expected in CI, but the command must diagnose and not install.
        self.assertIn('[DB Context]', result.stdout + result.stderr)
        self.assertEqual(local.exists(), existed)


if __name__ == '__main__':
    unittest.main()
