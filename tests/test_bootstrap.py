"""Bootstrap selection tests; no packages downloaded or system env changed."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("db_bootstrap_test", ROOT / "scripts" / "run_mcp.py")
BOOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOT)


class BootstrapTests(unittest.TestCase):
    def test_probe_accepts_compatible_installed_python(self):
        result = BOOT._probe([sys.executable])
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result[1], (3, 10))

    def test_old_bootstrap_interpreter_falls_back_to_newer_python(self):
        with patch.object(BOOT, "_candidates", return_value=[["old-python"], [sys.executable]]):
            with patch.object(BOOT, "_probe", side_effect=[None, (sys.executable, (3, 13))]) as probe:
                self.assertEqual(BOOT.find_python(), (sys.executable, (3, 13)))
                self.assertEqual(probe.call_count, 2)

    def test_no_supported_interpreter_is_actionable(self):
        with patch.object(BOOT, "_candidates", return_value=[["old-python"]]):
            with patch.object(BOOT, "_probe", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "TOHRU_DB_PYTHON"):
                    BOOT.find_python()

    def test_selected_python_override_is_considered(self):
        with patch.object(BOOT, "VENV_PYTHON", Path("/nonexistent/project/python")):
            with patch.dict(BOOT.os.environ, {"TOHRU_DB_PYTHON": sys.executable}):
                candidates = BOOT._candidates()
                self.assertEqual(candidates[0], [sys.executable])

    def test_probe_rejects_unsupported_version(self):
        class FakeProcess:
            returncode = 0
            stdout = "3.9|/usr/bin/python3\n"
        with patch.object(BOOT.subprocess, "run", return_value=FakeProcess()):
            self.assertIsNone(BOOT._probe(["legacy-python"]))

    def test_no_forced_architecture(self):
        self.assertNotIn("arm64", BOOT.boot.__code__.co_names)
        self.assertNotIn("platform", BOOT.boot.__code__.co_names)


if __name__ == "__main__":
    unittest.main()
