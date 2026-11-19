"""Portable MCP bootstrap: discover a usable installed Python and start a local venv.

This launcher deliberately supports older Python 3 interpreters so that a system
Python that cannot run the MCP SDK can still locate another installed Python.
Only stderr is used for diagnostics; stdout belongs exclusively to MCP JSON-RPC.
"""
from __future__ import print_function

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".venv"
VENV_PYTHON = ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
MARKER = ENV / ".drivers_sha256"
REQ = ROOT / "requirements.txt"
# Required by the official MCP Python SDK (not a platform or architecture restriction).
MIN_PYTHON = (3, 10)
PROBE = (
    "import sys,venv,ssl,sqlite3,ensurepip; "
    "print(str(sys.version_info[0])+'.'+str(sys.version_info[1])+'|'+sys.executable)"
)


def _add(candidates, seen, parts):
    if not parts:
        return
    key = tuple(str(part) for part in parts)
    if key not in seen:
        candidates.append(list(key))
        seen.add(key)


def _candidates():
    """Prefer an already working venv, then explicitly selected and installed Pythons."""
    candidates, seen = [], set()
    configured = os.environ.get("TOHRU_DB_PYTHON", "").strip()
    if configured:
        # A path or command name, not a shell command (no shell execution).
        _add(candidates, seen, [configured])
    if VENV_PYTHON.is_file():
        _add(candidates, seen, [str(VENV_PYTHON)])
    _add(candidates, seen, [sys.executable])

    names = ["python3", "python"]
    names.extend("python3.%s" % minor for minor in range(15, 9, -1))
    for name in names:
        resolved = shutil.which(name)
        if resolved:
            _add(candidates, seen, [resolved])

    # Common install locations are useful when an IDE does not inherit a login PATH.
    if os.name == "posix":
        prefixes = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/opt/local/bin"]
        for prefix in prefixes:
            for name in names:
                path = Path(prefix) / name
                if path.is_file() and os.access(str(path), os.X_OK):
                    _add(candidates, seen, [str(path)])
        home = Path.home()
        for root in (
            home / ".pyenv" / "versions", home / ".asdf" / "installs" / "python",
            home / ".local" / "share" / "mise" / "installs" / "python",
            home / ".local" / "share" / "uv" / "python",
            Path("/Library/Frameworks/Python.framework/Versions"),
        ):
            if root.is_dir():
                for item in sorted(root.iterdir(), reverse=True):
                    for path in (item / "bin" / "python3", item / "bin" / "python"):
                        if path.is_file() and os.access(str(path), os.X_OK):
                            _add(candidates, seen, [str(path)])
    else:
        home_python = Path.home() / "AppData" / "Local" / "Programs" / "Python"
        if home_python.is_dir():
            for item in sorted(home_python.iterdir(), reverse=True):
                path = item / "python.exe"
                if path.is_file():
                    _add(candidates, seen, [str(path)])
        launcher = shutil.which("py")
        if launcher:
            _add(candidates, seen, [launcher, "-3"])
            for minor in range(15, 9, -1):
                _add(candidates, seen, [launcher, "-3.%s" % minor])
    return candidates


def _probe(candidate):
    try:
        result = subprocess.run(
            candidate + ["-c", PROBE], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=8, check=False,
        )
        if result.returncode != 0:
            return None
        output = result.stdout.strip().splitlines()[-1]
        version, executable = output.split("|", 1)
        major, minor = (int(x) for x in version.split("."))
        if (major, minor) < MIN_PYTHON:
            return None
        if not Path(executable).is_file():
            return None
        return (executable, (major, minor))
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


def find_python():
    """Select the first interpreter capable of running the actual MCP dependency."""
    for candidate in _candidates():
        found = _probe(candidate)
        if found:
            return found
    raise RuntimeError(
        "No installed Python can run this MCP server. The MCP SDK requires Python "
        "3.10 or newer. Install a compatible Python or set TOHRU_DB_PYTHON to "
        "its executable path. No CPU architecture or operating system is required."
    )


def _working_install():
    if not MARKER.is_file() or not VENV_PYTHON.is_file():
        return False
    digest = hashlib.sha256(REQ.read_bytes()).hexdigest()
    if MARKER.read_text(encoding="utf-8").strip() != digest:
        return False
    check = subprocess.run(
        [str(VENV_PYTHON), "-c", "import mcp,pymysql,pg8000,oracledb,pytds"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=False,
    )
    return check.returncode == 0


def boot():
    """Compatibility launcher. Never installs packages without interactive setup approval."""
    executable, version = find_python()
    print("[DB Context] Found Python %s.%s: %s" % (version[0], version[1], executable), file=sys.stderr)
    if not _working_install():
        raise RuntimeError(
            "Project dependencies are missing/incomplete. Run setup.sh (macOS/Linux) "
            "or setup.ps1 (Windows) and approve installation. Nothing has been installed."
        )
    os.chdir(str(ROOT))
    argv = [str(VENV_PYTHON), str(ROOT / "scripts" / "mcp_server.py")]
    if os.name == "nt":
        raise SystemExit(subprocess.call(argv, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr))
    os.execv(str(VENV_PYTHON), argv)


if __name__ == "__main__":
    try:
        boot()
    except Exception as exc:
        print("[Tohru DB] MCP startup failed: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        sys.exit(1)
