"""SQL-dialect unit tests; these simulate drivers, not live database servers."""
from pathlib import Path
import json
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from db_engine import ConnectionSpec, _lookup_parent, exact_row_count


class StubCursor:
    description = [("id",), ("label",)]
    def __init__(self):
        self.statement = None
        self.params = None
        self.closed = False

    def execute(self, statement, params):
        self.statement = statement
        self.params = params

    def fetchone(self):
        return (7, "Alice")

    def close(self):
        self.closed = True


class StubConnection:
    def __init__(self):
        self.latest = StubCursor()

    def cursor(self):
        return self.latest


class DialectTests(unittest.TestCase):
    def test_exact_counts_all_engines_quote_identifiers_and_use_correct_table_paths(self):
        formats = {
            "mysql": "`store`.`line items`",
            "mariadb": "`store`.`line items`",
            "postgresql": '"public"."line items"',
            "sqlserver": "[dbo].[line items]",
            "oracle": '"HR"."line items"',
            "sqlite": '"line items"',
        }
        for engine, table_ref in formats.items():
            with self.subTest(engine=engine):
                spec = ConnectionSpec(engine=engine, path="sample.db" if engine == "sqlite" else "")
                object_ref = {"schema": {"sqlite": "main", "oracle": "HR", "sqlserver": "dbo"}.get(engine, "public"),
                              "name": "line items"}
                statements = []
                def simulate(_conn, query):
                    statements.append(query)
                    return [{"total_rows": 12}]
                with patch("db_engine.execute_rows", side_effect=simulate):
                    self.assertEqual(exact_row_count(None, spec, "store", object_ref, []), 12)
                self.assertEqual(statements, [f"SELECT COUNT(*) AS total_rows FROM {table_ref}"])

    def test_parent_lookup_uses_bound_values_each_engine(self):
        for engine in ("mysql", "mariadb", "postgresql", "sqlserver", "oracle", "sqlite"):
            with self.subTest(engine=engine):
                conn = StubConnection()
                spec = ConnectionSpec(engine=engine, path="sample.db" if engine == "sqlite" else "")
                obj = {"schema": "HR" if engine == "oracle" else "public", "name": "parents"}
                result = _lookup_parent(conn, spec, "store", obj, ["id", "label"], (7, "Alice"))
                self.assertEqual(result, {"id": 7, "label": "Alice"})
                self.assertEqual(conn.latest.params, (7, "Alice"))
                self.assertTrue(conn.latest.closed)
                sql = conn.latest.statement
                if engine == "oracle":
                    self.assertIn(":1", sql)
                    self.assertIn(":2", sql)
                    self.assertIn("FETCH FIRST 1 ROWS", sql)
                elif engine == "sqlite":
                    self.assertEqual(sql.count("?"), 2)
                else:
                    self.assertEqual(sql.count("%s"), 2)
                if engine == "sqlserver":
                    self.assertIn("TOP (1)", sql)

    def test_project_config_and_agent_files_exist(self):
        cfg = json.loads((ROOT / "opencode.jsonc").read_text())
        self.assertEqual(cfg["default_agent"], "tohru-db")
        self.assertTrue((ROOT / ".opencode/agents/tohru-db.md").exists())
        self.assertTrue((ROOT / ".opencode/commands/export-db.md").exists())
        self.assertTrue((ROOT / ".OpenCode").exists())
        self.assertEqual(cfg["mcp"]["servers"]["tohru_db"]["command"][-1], "scripts/run_mcp.py")


if __name__ == "__main__":
    unittest.main()
