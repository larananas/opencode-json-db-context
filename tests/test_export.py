"""Integration checks for the V3 single-JSON export contract using SQLite."""
from contextlib import closing
import datetime as dt
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from db_engine import (ConnectionSpec, _select_objects, export_database, json_value,
                       quote_ident, slug, validate_document)


class SQLiteExportTest(unittest.TestCase):
    def setUp(self):
        (ROOT / ".local").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / ".local")
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "demo.sqlite"
        with closing(sqlite3.connect(self.db)) as conn:
            conn.executescript("""
                PRAGMA foreign_keys=ON;
                CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                                    binary_data BLOB, score NUMERIC DEFAULT 0 CHECK(score>=0));
                CREATE TABLE projects (id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL,
                                       FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE CASCADE);
                CREATE VIEW user_names AS SELECT id,name FROM users;
                CREATE TRIGGER ensure_name AFTER INSERT ON users BEGIN SELECT 1; END;
                INSERT INTO users (id,name,binary_data,score) VALUES
                    (1,'Alice',X'00FF',4),(2,'Bob',NULL,7);
                INSERT INTO projects (id,owner_id) VALUES (10,1);
                CREATE TABLE empty_table (id TEXT PRIMARY KEY);
                CREATE TABLE large_text (id INTEGER PRIMARY KEY, content TEXT, payload BLOB);
            """)
            conn.execute("INSERT INTO large_text VALUES (1, ?, ?)", ("Z" * 1000, bytes(range(256)) * 3))
            conn.commit()
        self.spec = ConnectionSpec(engine="sqlite", path=str(self.db))
        self.fixed = dt.datetime(2026, 11, 18, 15, 23, tzinfo=dt.timezone.utc)
        self.output = Path(self.tmp.name) / "export"

    def export(self, **kwargs):
        return export_database(self.spec, output_dir=self.output, now=self.fixed, **kwargs)

    @staticmethod
    def read(summary):
        return json.loads(Path(summary["json"]).read_text(encoding="utf-8"))

    def test_server_one_json_counts_relations_views_and_validation(self):
        result = self.export(scope="server")
        data = self.read(result)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(data["export_metadata"]["database_count"], 1)
        self.assertEqual(result["summary"]["table_count"], 4)
        self.assertEqual(result["summary"]["total_rows_exact"], 4)
        self.assertEqual(result["summary"]["sampled_rows"], 4)
        self.assertEqual(data["summary"]["per_database"]["demo.sqlite"]["total_rows_exact"], 4)
        self.assertTrue(data["validation"]["schema_complete"])
        self.assertTrue(data["validation"]["sampling_complete"])
        self.assertTrue(data["validation"]["row_counts_complete"])
        tables = data["databases"]["demo.sqlite"]["schemas"]["main"]["tables"]
        self.assertEqual(tables["users"]["total_rows"], 2)
        self.assertEqual(tables["users"]["row_count_status"], "exact")
        self.assertEqual(tables["users"]["sample_count"], 2)
        self.assertEqual(tables["empty_table"]["total_rows"], 0)
        self.assertEqual(tables["empty_table"]["sample_rows"], [])
        self.assertEqual(len(data["relationships"]), 1)
        self.assertEqual(data["relationships"][0]["source_columns"], ["owner_id"])
        self.assertIn("user_names", data["databases"]["demo.sqlite"]["schemas"]["main"]["views"])
        self.assertEqual(len(data["databases"]["demo.sqlite"]["metadata"]["triggers"]), 1)
        self.assertEqual([f.suffix for f in self.output.iterdir()], [".json"])
        self.assertEqual(set(result), {"status", "json", "statistics", "summary", "database_names", "note"})

    def test_scope_one_table_preserves_name_and_does_not_include_others(self):
        result = self.export(scope="table", database="demo.sqlite", table="users")
        data = self.read(result)
        self.assertEqual(result["summary"]["table_count"], 1)
        self.assertTrue(Path(result["json"]).name.endswith("demo_sqlite_users.json"))
        tables = data["databases"]["demo.sqlite"]["schemas"]["main"]["tables"]
        self.assertEqual(set(tables), {"users"})
        self.assertEqual(len(data["databases"]["demo.sqlite"]["metadata"]["triggers"]), 1)

    def test_multiple_tables_scoped_and_table_only_extras(self):
        result = self.export(scope="tables", database="demo.sqlite", table_names=["projects", "users"])
        data = self.read(result)
        tables = data["databases"]["demo.sqlite"]["schemas"]["main"]["tables"]
        self.assertEqual(set(tables), {"projects", "users"})
        self.assertEqual(result["summary"]["total_rows_exact"], 3)
        self.assertEqual(len(data["relationships"]), 1)
        self.assertTrue(Path(result["json"]).name.endswith("demo_sqlite_selected_tables.json"))
        with self.assertRaisesRegex(ValueError, "not found uniquely"):
            self.export(scope="tables", database="demo.sqlite", table_names=["projects", "missing"])
        with self.assertRaisesRegex(ValueError, "Duplicate table"):
            self.export(scope="tables", database="demo.sqlite", table_names=["users", "users"])

    def test_ambiguous_table_names_are_not_silently_picked(self):
        objects = [{"schema": "a", "name": "users", "type": "table"},
                   {"schema": "b", "name": "users", "type": "table"}]
        with self.assertRaisesRegex(ValueError, "not found uniquely"):
            _select_objects(objects, "tables", "app", "", "", ["users"])
        self.assertEqual(_select_objects(objects, "tables", "app", "", "", ["a.users"]), objects[:1])
        self.assertEqual(_select_objects(objects, "tables", "app", "a", "", ["users"]), objects[:1])

    def test_database_list_and_scope_validation(self):
        result = self.export(scope="databases", database_names=["demo.sqlite"])
        self.assertEqual(result["summary"]["table_count"], 4)
        self.assertTrue(Path(result["json"]).name.endswith("demo_demo_sqlite.json"))
        with self.assertRaisesRegex(ValueError, "not accessible"):
            self.export(scope="databases", database_names=["other.sqlite"])
        with self.assertRaisesRegex(ValueError, "Duplicate database"):
            self.export(scope="databases", database_names=["demo.sqlite", "demo.sqlite"])
        with self.assertRaises(ValueError):
            self.export(scope="server", sample_limit=21)
        with self.assertRaises(ValueError):
            self.export(scope="everything")
        with self.assertRaises(ValueError):
            self.export(scope="tables", database="demo.sqlite", table_names=[])

    def test_existing_csv_and_other_artifacts_removed_only_after_success(self):
        self.output.mkdir()
        for filename in ("old_schema.csv", "old_samples.csv", "some.log", "stale.tmp", ".gitkeep"):
            (self.output / filename).write_text("old")
        old = self.output / "existing_export.json"
        old.write_text("{}")
        failure = None
        try:
            self.export(scope="table", database="demo.sqlite", table="missing")
        except ValueError as exc:
            failure = exc
        self.assertIsNotNone(failure)
        self.assertTrue((self.output / "old_schema.csv").exists())  # never clean on failure
        result = self.export(scope="table", database="demo.sqlite", table="users")
        self.assertEqual({f.name for f in self.output.iterdir()}, {"existing_export.json", Path(result["json"]).name})

    def test_large_integer_pk_uses_index_pivot_no_full_sort(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.executemany("INSERT INTO projects (id, owner_id) VALUES (?, 1)", [(i,) for i in range(100, 320)])
            conn.commit()
        result = self.export(scope="table", database="demo.sqlite", table="projects", sample_threshold=100)
        self.assertEqual(result["status"], "ok", result)
        t = self.read(result)["databases"]["demo.sqlite"]["schemas"]["main"]["tables"]["projects"]
        self.assertEqual(t["total_rows"], 221)
        self.assertEqual(t["sample_method"], "approximate_pk_pivot")
        self.assertEqual(t["sample_count"], 20)
        ids = [r["id"] for r in t["sample_rows"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(any("non-uniform" in w for w in result["statistics"]["warnings"]))

    def test_large_table_no_pk_reports_partial_without_expensive_sort(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("CREATE TABLE no_pk (value TEXT)")
            conn.executemany("INSERT INTO no_pk VALUES (?)", [(str(i),) for i in range(110)])
            conn.commit()
        result = self.export(scope="table", database="demo.sqlite", table="no_pk", sample_threshold=100)
        t = self.read(result)["databases"]["demo.sqlite"]["schemas"]["main"]["tables"]["no_pk"]
        self.assertEqual(result["status"], "partial")
        self.assertEqual(t["sample_status"], "skipped_large_table")
        self.assertEqual(t["total_rows"], 110)
        retry = self.export(scope="table", database="demo.sqlite", table="no_pk",
                            sample_threshold=100, expensive_sampling=True)
        self.assertEqual(retry["status"], "ok", retry)
        self.assertEqual(self.read(retry)["databases"]["demo.sqlite"]["schemas"]["main"]["tables"]["no_pk"]["sample_count"], 20)

    def test_coherent_parent_samples_are_opt_in(self):
        result = self.export(scope="tables", database="demo.sqlite", table_names=["users", "projects"],
                             sample_limit=1, coherent_samples=True)
        data = self.read(result)
        tables = data["databases"]["demo.sqlite"]["schemas"]["main"]["tables"]
        self.assertEqual(tables["users"]["sample_count"], 1)
        self.assertEqual(tables["projects"]["sample_count"], 1)
        self.assertEqual(tables["users"]["sample_rows"][0]["id"], tables["projects"]["sample_rows"][0]["owner_id"])
        self.assertEqual(data["databases"]["demo.sqlite"]["coherent_sampling"]["unresolved_links"], 0)
        self.assertTrue(data["validation"]["sampling_complete"])
        no_parent = self.export(scope="table", database="demo.sqlite", table="projects", coherent_samples=True)
        self.assertEqual(no_parent["summary"]["table_count"], 1)
        self.assertEqual(no_parent["status"], "ok")  # FK target intentionally outside scope

    def test_implicit_sqlite_foreign_key_resolves_parent_primary_key(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.executescript("""
                CREATE TABLE implicit_fk (id INTEGER PRIMARY KEY,
                                          owner_id INTEGER REFERENCES users);
                INSERT INTO implicit_fk VALUES (1, 1);
            """)
        result = self.export(scope="tables", database="demo.sqlite",
                             table_names=["users", "implicit_fk"], coherent_samples=True)
        data = self.read(result)
        fk = data["databases"]["demo.sqlite"]["schemas"]["main"]["tables"]["implicit_fk"]["foreign_keys"][0]
        self.assertEqual(fk["target_columns"], ["id"])
        self.assertTrue(data["validation"]["schema_complete"])
        self.assertEqual(result["status"], "ok", result)

    def test_compact_large_text_and_blob_is_optional(self):
        normal = self.export(scope="table", database="demo.sqlite", table="large_text")
        original = self.read(normal)["databases"]["demo.sqlite"]["schemas"]["main"]["tables"]["large_text"]["sample_rows"][0]
        self.assertEqual(len(original["content"]), 1000)
        self.assertEqual(original["payload"]["$type"], "base64")
        compact = self.export(scope="table", database="demo.sqlite", table="large_text",
                              compact_large_values=True, max_value_bytes=256)
        sample = self.read(compact)["databases"]["demo.sqlite"]["schemas"]["main"]["tables"]["large_text"]["sample_rows"][0]
        self.assertEqual(sample["content"]["$type"], "omitted_large_text")
        self.assertEqual(sample["content"]["byte_length"], 1000)
        self.assertEqual(len(sample["content"]["sha256"]), 64)
        self.assertEqual(sample["payload"]["$type"], "omitted_large_binary")
        self.assertEqual(sample["payload"]["byte_length"], 768)

    def test_failed_count_is_null_and_export_partial(self):
        with patch("db_engine.exact_row_count", return_value=None):
            result = self.export(scope="table", database="demo.sqlite", table="users")
        obj = self.read(result)["databases"]["demo.sqlite"]["schemas"]["main"]["tables"]["users"]
        self.assertIsNone(obj["total_rows"])
        self.assertEqual(obj["row_count_status"], "error")
        self.assertIsNone(result["summary"]["total_rows_exact"])
        self.assertFalse(result["statistics"]["row_counts_complete"])
        self.assertEqual(result["status"], "partial")

    def test_validator_detects_corrupt_row_shape_and_missing_objects(self):
        data = self.read(self.export(scope="table", database="demo.sqlite", table="users"))
        selected = {"demo.sqlite": [{"schema": "main", "name": "users", "type": "table"}]}
        issues = []
        data["databases"]["demo.sqlite"]["schemas"]["main"]["tables"]["users"]["sample_rows"][0].pop("id")
        validate_document(data, selected, 20, issues)
        self.assertTrue(any("shape mismatch" in issue for issue in issues))
        issues.clear()
        validate_document(data, {"demo.sqlite": []}, 20, issues)
        self.assertTrue(any("Object mismatch" in issue for issue in issues))

    def test_collision_keeps_previous_json_exports(self):
        a = self.export(scope="server")
        b = self.export(scope="server")
        self.assertNotEqual(a["json"], b["json"])
        self.assertEqual(len(list(self.output.iterdir())), 2)
        self.assertTrue(Path(a["json"]).exists())
        self.assertTrue(Path(b["json"]).exists())

    def test_safe_identifiers_and_lossless_tags(self):
        self.assertEqual(quote_ident("sqlserver", "a]b"), "[a]]b]")
        self.assertEqual(quote_ident("mariadb", "a`b"), "`a``b`")
        self.assertEqual(quote_ident("postgresql", 'a"b'), '"a""b"')
        self.assertEqual(slug(" server / 2026 "), "server_2026")
        self.assertEqual(json_value(Decimal("1.234567890123456789")),
                         {"$type": "decimal", "value": "1.234567890123456789"})
        self.assertEqual(json_value(b"\x00\xff"), {"$type": "base64", "value": "AP8="})

    def test_sqlite_connection_is_readonly(self):
        from db_engine import open_connection
        conn = open_connection(self.spec)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("INSERT INTO users(name) VALUES('Eve')")
        conn.close()


if __name__ == "__main__":
    unittest.main()
