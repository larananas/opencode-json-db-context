"""Exercise MCP application logic with a test stub, without requiring PyPI access."""
from __future__ import annotations
import importlib.util
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


class FakeMcp:
    def __init__(self,*args,**kwargs):
        pass

    def tool(self):
        return lambda f:f


def load_module():
    fake_mcp=types.ModuleType("mcp")
    fake_server=types.ModuleType("mcp.server")
    fake_fast=types.ModuleType("mcp.server.fastmcp")
    fake_fast.FastMCP=FakeMcp
    with patch.dict(sys.modules,{"mcp":fake_mcp,"mcp.server":fake_server,"mcp.server.fastmcp":fake_fast}):
        module_spec=importlib.util.spec_from_file_location("db_mcp_test",ROOT/"scripts"/"mcp_server.py")
        module=importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
    return module


class MpcToolsTest(unittest.TestCase):
    def setUp(self):
        (ROOT / ".local").mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=ROOT / ".local")
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"demo.sqlite"
        with closing(sqlite3.connect(self.path)) as con:
            con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, secret TEXT)")
            con.execute("INSERT INTO t VALUES (1, 'private-row-value')")
            con.commit()
        self.app=load_module()

    def test_connect_inspect_export_and_disconnect_without_leak(self):
        caps = self.app.db_capabilities()
        self.assertIn("sqlite", caps["engines"][-1].lower())
        self.assertIn("tables", caps["scopes"])
        self.assertEqual(caps["default"]["formats"], ["one indented JSON"])
        connection=self.app.db_connect("sqlite",path=str(self.path),password="not-a-real-secret")
        self.assertEqual(connection["status"],"connected")
        self.assertNotIn("not-a-real-secret",str(connection))
        summary=self.app.db_inspect()
        self.assertIn("demo.sqlite",summary["databases"])
        self.assertIn("t",[o["name"] for o in summary["databases"]["demo.sqlite"]["objects"]])
        from db_engine import export_database
        with patch.object(self.app,"export_database",side_effect=lambda *args,**kw: export_database(*args,**{**kw,"output_dir":Path(self.temp.name)/"export"})):
            exported=self.app.db_export(scope="table",database="demo.sqlite",table="t")
        self.assertEqual(exported["status"],"ok",exported)
        self.assertNotIn("private-row-value", str(exported))
        self.assertNotIn("schema_csv", exported)
        self.assertNotIn("samples_csv", exported)
        self.assertIn("private-row-value",Path(exported["json"]).read_text())
        self.assertEqual(self.app.db_disconnect()["status"],"disconnected")
        with self.assertRaisesRegex(ValueError,"Not connected"):
            self.app.db_inspect()


if __name__=="__main__":
    unittest.main()
