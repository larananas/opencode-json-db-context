"""Credential resolution, local-secret confinement and default-export contracts."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from env_config import load_local_env, parse_dotenv, resolve_env
from test_mcp_tools import load_module


class DotEnvTests(unittest.TestCase):
    def test_laravel_mariadb_connection_and_password_quoting(self):
        env = '''# A sample Docker configuration
            export DB_CONNECTION=mariadb
            DB_HOST=db
            DB_PORT=3306
            DB_DATABASE=app
            DB_USERNAME="demo"
            DB_PASSWORD="with # spaces and \\" quotes"
        '''
        result = resolve_env(env)
        self.assertEqual(result.spec.engine, "mariadb")
        self.assertEqual(result.spec.host, "db")
        self.assertEqual(result.spec.database, "app")
        self.assertEqual(result.spec.password, 'with # spaces and " quotes')
        self.assertTrue(any("Docker" in w for w in result.warnings))
        self.assertNotIn(result.spec.password, str(result.recognized_keys) + str(result.warnings))

    def test_database_url_decodes_auth_and_overrides_generic_variables(self):
        resolved = resolve_env("DATABASE_URL=postgresql+pg8000://u%40x:p%23%40ss@127.0.0.1:5433/app_db\nDB_HOST=unused")
        self.assertEqual(resolved.spec.engine, "postgresql")
        self.assertEqual(resolved.spec.user, "u@x")
        self.assertEqual(resolved.spec.password, "p#@ss")
        self.assertEqual(resolved.spec.port, 5433)
        self.assertEqual(resolved.spec.database, "app_db")

    def test_common_mysql_sqlserver_oracle_and_sqlite(self):
        cases = [
            ("MYSQL_HOST=localhost\nMYSQL_USER=foo\nMYSQL_PASSWORD=secret", "mysql"),
            ("DB_CONNECTION=sqlsrv\nDB_HOST=localhost\nDB_PORT=1433", "sqlserver"),
            ("DATABASE_URL=oracle+oracledb://u:p@localhost:1521/?service_name=XEPDB1", "oracle"),
            ("DB_CONNECTION=sqlite\nDB_DATABASE=.local/demo.sqlite", "sqlite"),
            ("POSTGRES_HOST=localhost\nPOSTGRES_USER=foo", "postgresql"),
        ]
        for text, expected in cases:
            with self.subTest(expected=expected):
                result = resolve_env(text)
                self.assertEqual(result.spec.engine, expected)
        self.assertEqual(resolve_env(cases[2][0]).spec.service, "XEPDB1")

    def test_preserves_hash_in_unquoted_password_and_does_not_evaluate_shell(self):
        result = parse_dotenv("DB_PASSWORD=secret#fragment\nDB_HOST=127.0.0.1 # comment")
        self.assertEqual(result["DB_PASSWORD"], "secret#fragment")
        self.assertEqual(result["DB_HOST"], "127.0.0.1")
        with self.assertRaisesRegex(ValueError, "Unresolved environment"):
            resolve_env("DB_HOST=$(dangerous_command)\nDB_PASSWORD=secret")

    def test_rejects_unsupported_multiple_urls_and_malformed_ports_without_secret_leaks(self):
        for source in ("DATABASE_URL=redis://admin:private@localhost/1",
                       "DATABASE_URL=mysql://u:secret@localhost/db\nDB_URL=postgresql://u:secret@localhost/db",
                       "DB_CONNECTION=mysql\nDB_PORT=bad\nDB_PASSWORD=secret"):
            with self.subTest(source=source.splitlines()[0].split("=")[0]):
                with self.assertRaises(ValueError) as ctx:
                    resolve_env(source)
                self.assertNotIn("secret", str(ctx.exception))
                self.assertNotIn("private", str(ctx.exception))

    def test_missing_engine_returns_actionable_error(self):
        with self.assertRaisesRegex(ValueError, "identify SQL engine"):
            resolve_env("CUSTOM_SECRET=secret\nFOO=bar")

    def test_file_confinement_no_external_path_or_symlink(self):
        with tempfile.TemporaryDirectory() as project_dir, tempfile.TemporaryDirectory() as outside_dir:
            root = Path(project_dir)
            local = root / ".local"
            local.mkdir()
            (local / ".env").write_text("DB_CONNECTION=mariadb\nDB_PASSWORD=never-print\n")
            self.assertIn("never-print", load_local_env(".env", root=root))
            self.assertIn("never-print", load_local_env(".local/.env", root=root))
            outside = Path(outside_dir) / "outside.env"
            outside.write_text("DB_PASSWORD=hidden")
            with self.assertRaisesRegex(ValueError, "relative"):
                load_local_env(str(outside), root=root)
            link = local / "linked.env"
            try:
                link.symlink_to(outside)
            except OSError:
                return
            with self.assertRaisesRegex(ValueError, "inside .local"):
                load_local_env("linked.env", root=root)
            with self.assertRaises(ValueError):
                load_local_env("../other.env", root=root)

    def test_local_file_size_limit(self):
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            local = root / ".local"
            local.mkdir()
            (local / "large.env").write_text("Z=" + "A" * 140000)
            with self.assertRaisesRegex(ValueError, "128 KiB"):
                load_local_env("large.env", root=root)


class DotenvMcpTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".local").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / ".local")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "fixture.sqlite"
        conn = sqlite3.connect(self.path)
        try:
            conn.executescript("CREATE TABLE users(id INTEGER PRIMARY KEY); INSERT INTO users VALUES (1);")
            conn.commit()
        finally:
            conn.close()
        self.app = load_module()

    def test_env_connection_exports_server_by_default_without_returning_rows(self):
        from db_engine import export_database
        target = Path(self.temporary.name) / "export"
        with patch.object(self.app, "export_database", side_effect=lambda *args, **kw: export_database(*args, **{**kw, "output_dir": target})) as exporter:
            result = self.app.db_connect_env(env_text=f"DB_CONNECTION=sqlite\nDB_DATABASE={self.path}\nDB_PASSWORD=private-marker")
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(exporter.call_args.kwargs["scope"], "server")
        self.assertTrue(exporter.call_args.kwargs["coherent_samples"])
        self.assertEqual(result["export"]["summary"]["table_count"], 1)
        self.assertNotIn("private-marker", json.dumps(result))
        self.assertEqual([f.suffix for f in target.iterdir()], [".json"])

    def test_connect_only_does_not_trigger_export(self):
        with patch.object(self.app, "export_database") as export:
            connected = self.app.db_connect_env(env_text=f"DB_CONNECTION=sqlite\nDB_DATABASE={self.path}", connect_only=True)
        self.assertEqual(connected["status"], "connected")
        export.assert_not_called()

    def test_specific_table_never_widens_scope(self):
        from db_engine import export_database
        target = Path(self.temporary.name) / "output"
        with patch.object(self.app, "export_database", side_effect=lambda *args, **kw: export_database(*args, **{**kw, "output_dir": target})):
            result = self.app.db_connect_env(env_text=f"DB_CONNECTION=sqlite\nDB_DATABASE={self.path}", scope="table", table="users")
        self.assertEqual(result["export"]["summary"]["table_count"], 1)
        self.assertEqual(result["export"]["database_names"], [self.path.name])

    def test_local_file_import_stays_local_and_does_not_return_password(self):
        secret_file = Path(self.temporary.name) / "connection.env"
        # The test-local .env must remain inside .local and never be returned.
        secret_file.write_text(f"DB_CONNECTION=sqlite\nDB_DATABASE={self.path}\nDB_PASSWORD=local-only-secret")
        with patch.object(self.app, "export_database") as exporter:
            result = self.app.db_connect_env(env_file=str(secret_file.relative_to(ROOT / ".local")), connect_only=True)
        exporter.assert_not_called()
        self.assertNotIn("local-only-secret", repr(result))

    def test_manual_connect_and_export_server_scope_default(self):
        from db_engine import export_database
        with patch.object(self.app, "export_database", side_effect=lambda *args, **kw: export_database(*args, **{**kw, "output_dir": Path(self.temporary.name) / "output"})) as exporter:
            result = self.app.db_connect_and_export(engine="sqlite", path=str(self.path))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(exporter.call_args.kwargs["scope"], "server")


if __name__ == "__main__":
    unittest.main()

# Extra Docker and failure-mode regression checks.
class DockerRegressionTests(unittest.TestCase):
    def test_docker_root_account_is_supported_with_warning(self):
        settings = resolve_env('MYSQL_DATABASE=sample\nMYSQL_ROOT_PASSWORD=private-root\nMYSQL_HOST=mysql')
        self.assertEqual(settings.spec.user, 'root')
        self.assertEqual(settings.spec.password, 'private-root')
        self.assertTrue(any('read-only' in w for w in settings.warnings))
        self.assertNotIn('private-root', repr(settings.warnings))

    def test_failed_docker_connection_provides_safe_diagnostic(self):
        app = load_module()
        with patch.object(app, 'open_connection', side_effect=RuntimeError('password=do-not-leak')):
            result = app.db_connect_env(env_text='DB_CONNECTION=mariadb\nDB_HOST=db\nDB_PORT=3306\nDB_PASSWORD=do-not-leak')
        self.assertEqual(result['status'], 'connection_failed')
        self.assertNotIn('do-not-leak', repr(result))
        self.assertTrue(any('Docker' in w for w in result['warnings']))

class ExplicitScopeContractTests(unittest.TestCase):
    def test_database_from_env_is_only_used_when_scope_explicit(self):
        app = load_module()
        class MockCursor:
            def execute(self, query):
                return None
            def fetchone(self):
                return (1,)
            def close(self):
                return None
        class MockConnection:
            def cursor(self):
                return MockCursor()
            def close(self):
                return None
        with patch.object(app, 'open_connection', return_value=MockConnection()), \
             patch.object(app, 'engine_version', return_value='11.8'), \
             patch.object(app, 'export_database', return_value={'status':'ok', 'summary':{}}) as exporter:
            response = app.db_connect_env(env_text='DB_CONNECTION=mariadb\nDB_DATABASE=from_env\nDB_HOST=localhost\nDB_USERNAME=test\nDB_PASSWORD=private')
            self.assertEqual(response['status'], 'ok')
            self.assertEqual(exporter.call_args.kwargs['scope'], 'server')
            self.assertEqual(exporter.call_args.kwargs['database'], '')
            app.db_connect_env(env_text='DB_CONNECTION=mariadb\nDB_DATABASE=from_env', scope='table', table='users')
            self.assertEqual(exporter.call_args.kwargs['database'], 'from_env')

    def test_local_directory_cannot_be_symlinked_outside_repository(self):
        with tempfile.TemporaryDirectory() as rootdir, tempfile.TemporaryDirectory() as outdir:
            root = Path(rootdir)
            external = Path(outdir)
            (external / 'secret.env').write_text('DB_PASSWORD=outside')
            try:
                (root / '.local').symlink_to(external, target_is_directory=True)
            except OSError:
                self.skipTest('Directory symlinks not available')
            with self.assertRaisesRegex(ValueError, 'remain inside'):
                load_local_env('secret.env', root=root)
