"""Regression checks for privacy, TLS, publication and privileged exports."""
from __future__ import annotations

from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import ssl
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from db_engine import ConnectionSpec, export_database, open_connection, tls_context


class HardeningTests(unittest.TestCase):
    def test_no_case_conflicting_marker_and_no_private_files_tracked(self):
        self.assertFalse((ROOT / '.OpenCode').is_file())
        p = ROOT / 'scripts' / 'check_publication.py'
        spec = importlib.util.spec_from_file_location('publication_hardening', p)
        check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(check)
        self.assertEqual(check.check(), [])
        with patch.object(check, 'candidate_names', return_value=['.OpenCode', '.opencode/agents/a.md']):
            self.assertTrue(any('conflict' in msg.lower() for msg in check.check()))

    def test_permissions_deny_search_secret_paths_and_execute_without_prompts(self):
        cfg = json.loads((ROOT / 'opencode.jsonc').read_text(encoding='utf-8'))
        rules = cfg['permissions']
        for action in ('shell', 'edit', 'external_directory', 'glob', 'grep', 'execute'):
            self.assertIn({'action': action, 'resource': '*', 'effect': 'deny'}, rules)
        for name in ('*.env', '*.local/*', '*export/*'):
            self.assertIn({'action': 'read', 'resource': name, 'effect': 'deny'}, rules)
        self.assertIn({'action':'tohru_db_*', 'resource':'*', 'effect':'allow'}, rules)

    def test_tls_modes_validated_and_ca_path_constrained(self):
        with self.assertRaisesRegex(ValueError, 'tls_mode'):
            ConnectionSpec(engine='mysql', tls_mode='disable')
        with self.assertRaisesRegex(ValueError, 'not applicable'):
            ConnectionSpec(engine='sqlite', path='demo.sqlite', tls_mode='require')
        ctx = tls_context(ConnectionSpec(engine='mysql', tls_mode='verify_identity'))
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        ctx = tls_context(ConnectionSpec(engine='postgresql', tls_mode='verify_ca'))
        self.assertFalse(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        ctx = tls_context(ConnectionSpec(engine='mysql', tls_mode='require'))
        self.assertFalse(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_NONE)
        with self.assertRaises(ValueError):
            tls_context(ConnectionSpec(engine='mysql', tls_mode='verify_identity', tls_ca_file='/etc/passwd'))
        with self.assertRaisesRegex(ValueError, 'project-relative'):
            tls_context(ConnectionSpec(engine='postgresql', tls_mode='verify_identity', tls_ca_file='../unauthorized.pem'))

    def test_pymysql_and_pg8000_receive_tls_context(self):
        dummy=types.SimpleNamespace()
        mysql=types.SimpleNamespace(connect=Mock(return_value=dummy))
        pgdbapi=types.SimpleNamespace(connect=Mock(return_value=dummy))
        pg=types.ModuleType('pg8000')
        pg.dbapi=pgdbapi
        with patch.dict(sys.modules, {'pymysql':mysql, 'pg8000':pg, 'pg8000.dbapi':pgdbapi}):
            self.assertIs(open_connection(ConnectionSpec(engine='mysql', tls_mode='verify_identity')), dummy)
            self.assertTrue(mysql.connect.call_args.kwargs['ssl'].check_hostname)
            self.assertIs(open_connection(ConnectionSpec(engine='postgresql', tls_mode='verify_identity')), dummy)
            self.assertTrue(pgdbapi.connect.call_args.kwargs['ssl_context'].check_hostname)

    def test_unsupported_explicit_tls_fails_instead_of_downgrading(self):
        for engine in ('sqlserver','oracle'):
            with self.subTest(engine=engine):
                with self.assertRaisesRegex(ValueError, 'not supported'):
                    open_connection(ConnectionSpec(engine=engine, service='sample', tls_mode='verify_identity'))

    def test_dotenv_tls_parsing_does_not_reveal_credentials(self):
        from env_config import resolve_env
        result = resolve_env('DB_CONNECTION=postgresql\nDB_HOST=localhost\nDB_PASSWORD=private-password\nPGSSLMODE=verify-full')
        self.assertEqual(result.spec.tls_mode, 'verify_identity')
        self.assertNotIn('private-password', repr(result.spec))

    def test_sensitive_sample_redaction_is_opt_in(self):
        (ROOT / '.local').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / '.local') as tmp:
            path=Path(tmp)/'data.sqlite'
            with closing(sqlite3.connect(path)) as conn:
                conn.execute('CREATE TABLE customers (id INTEGER PRIMARY KEY, email TEXT, api_key TEXT, label TEXT)')
                conn.execute("INSERT INTO customers VALUES (1,'private@example.net','real-token-value','safe')")
                conn.commit()
            spec=ConnectionSpec(engine='sqlite',path=str(path))
            result=export_database(spec,scope='server',output_dir=Path(tmp)/'out1',redact_sensitive_data=True)
            self.assertEqual(result['status'],'ok',result)
            data=json.loads(Path(result['json']).read_text(encoding='utf-8'))
            raw=json.dumps(data)
            self.assertNotIn('private@example.net',raw)
            self.assertNotIn('real-token-value',raw)
            table=data['databases']['data.sqlite']['schemas']['main']['tables']['customers']
            self.assertEqual(table['sample_rows'][0]['label'],'safe')
            self.assertEqual(data['summary']['redacted_sample_values'],2)
            result=export_database(spec,scope='server',output_dir=Path(tmp)/'out2')
            self.assertIn('private@example.net',Path(result['json']).read_text(encoding='utf-8'))

    def test_admin_scope_requires_explicit_approval_and_preview_does_not_export(self):
        from test_mcp_tools import load_module
        from db_engine import ConnectionSpec
        app = load_module()
        app._spec = ConnectionSpec(engine='mysql', user='root', host='127.0.0.1')
        with patch.object(app, 'export_database') as exporter:
            denied = app.db_export(scope='server')
        self.assertEqual(denied['status'], 'confirmation_required')
        self.assertNotIn('password', str(denied).lower())
        exporter.assert_not_called()
        with patch.object(app, 'export_database', return_value={'status':'ok'}) as exporter:
            permitted = app.db_export(scope='server', approve_privileged_server_export=True)
        self.assertEqual(permitted['status'], 'ok')
        exporter.assert_called_once()
        (ROOT / '.local').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / '.local') as tmp:
            db=Path(tmp)/'preview.sqlite'
            with closing(sqlite3.connect(db)) as conn:
                conn.execute('CREATE TABLE t(id INTEGER PRIMARY KEY)');conn.commit()
            app._spec = ConnectionSpec(engine='sqlite', path=str(db))
            report = app.db_preview_export()
            self.assertEqual(report['table_count'], 1)
            self.assertEqual(report['status'], 'preview')
            self.assertFalse((Path(tmp)/'export').exists())

    def test_unrelated_non_json_file_is_preserved_outside_export(self):
        (ROOT / '.local').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / '.local') as tmp:
            base=Path(tmp); db=base/'db.sqlite'
            with closing(sqlite3.connect(db)) as conn:
                conn.execute('CREATE TABLE t(id INTEGER PRIMARY KEY)');conn.commit()
            folder=base/'export';folder.mkdir();(folder/'user-notes.txt').write_text('keep this')
            result=export_database(ConnectionSpec(engine='sqlite',path=str(db)),scope='server',output_dir=folder)
            self.assertEqual([p.suffix for p in folder.iterdir()],['.json'])
            self.assertEqual((base/'.local'/'recovered-exports'/'user-notes.txt').read_text(),'keep this')


if __name__=='__main__':
    unittest.main()
