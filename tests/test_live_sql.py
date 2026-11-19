"""Opt-in live SQL integration. Runs only with explicit ephemeral CI connection env.

Test-created tables exist only in the dedicated CI database. Never aim these
integration tests at production databases.
"""
from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from db_engine import ConnectionSpec, export_database, open_connection


@unittest.skipUnless(os.getenv('LIVE_ENGINE'), 'No live test database configured')
class LiveSqlIntegrationTest(unittest.TestCase):
    def test_export_two_related_tables(self):
        engine = os.environ['LIVE_ENGINE']
        db = os.environ['LIVE_DATABASE']
        if db != 'opencode_ci':
            self.fail('Live SQL integration requires exactly the isolated opencode_ci test database')
        spec = ConnectionSpec(engine=engine, host=os.environ['LIVE_HOST'],
                              port=int(os.environ['LIVE_PORT']), user=os.environ['LIVE_USER'],
                              password=os.environ['LIVE_PASSWORD'], database=db)
        conn = None
        for attempt in range(25):
            try:
                conn = open_connection(spec)
                break
            except Exception:
                if attempt == 24:
                    raise RuntimeError('CI test database did not become ready') from None
                time.sleep(2)
        assert conn is not None
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('CREATE TABLE context_v5_users (id INT PRIMARY KEY, name VARCHAR(80) NOT NULL)')
                cursor.execute('CREATE TABLE context_v5_orders (id INT PRIMARY KEY, user_id INT NOT NULL, FOREIGN KEY (user_id) REFERENCES context_v5_users(id))')
                cursor.execute("INSERT INTO context_v5_users (id, name) VALUES (1, 'Example'), (2, 'Another')")
                cursor.execute('INSERT INTO context_v5_orders (id, user_id) VALUES (10, 1)')
            if engine == 'postgresql':
                conn.commit()
        finally:
            conn.close()
        with tempfile.TemporaryDirectory() as export_dir:
            result = export_database(spec, scope='tables', database=db,
                table_names=['context_v5_users', 'context_v5_orders'],
                sample_limit=20, coherent_samples=True, output_dir=export_dir)
            self.assertEqual(result['status'], 'ok', result['statistics'])
            self.assertEqual(result['summary']['table_count'], 2)
            self.assertEqual(result['summary']['total_rows_exact'], 3)
            self.assertEqual(result['summary']['foreign_key_count'], 1)
            names = list(Path(export_dir).iterdir())
            self.assertEqual(len(names), 1)
            self.assertEqual(names[0].suffix, '.json')
            document = json.loads(names[0].read_text())
            schemas = document['databases'][db]['schemas']
            tables = next(v['tables'] for v in schemas.values() if 'context_v5_users' in v['tables'])
            self.assertEqual(tables['context_v5_users']['sample_count'], 2)
            self.assertEqual(tables['context_v5_orders']['sample_count'], 1)
            self.assertEqual(tables['context_v5_orders']['total_rows'], 1)
            self.assertEqual(document['relationships'][0]['target_table'], 'context_v5_users')
            self.assertEqual(tables['context_v5_orders']['sample_rows'][0]['user_id'], 1)
            self.assertTrue(document['validation']['schema_complete'])
            self.assertTrue(document['validation']['sampling_complete'])


if __name__ == '__main__':
    unittest.main()
