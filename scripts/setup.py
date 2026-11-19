"""Interactive, portable project setup using an already selected Python interpreter.

The POSIX/PowerShell wrappers locate Python. This script only writes inside the
project, and only installs Python packages after explicit confirmation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / '.venv'
REQ = ROOT / 'requirements.txt'
MARKER = ENV / '.drivers_sha256'
LOCAL_CONFIG = ROOT / '.opencode' / 'opencode.jsonc'
MIN_PYTHON = (3, 10)
IMPORTS = 'import mcp,pymysql,pg8000,oracledb,pytds,ssl,sqlite3'
MODULES = {'mcp':'MCP SDK', 'pymysql':'MariaDB/MySQL driver', 'pg8000':'PostgreSQL driver',
           'pytds':'SQL Server driver', 'oracledb':'Oracle driver'}


def venv_python() -> Path:
    return ENV / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def run(args: list[str], *, quiet: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(ROOT), check=False, stdout=subprocess.DEVNULL if quiet else None,
                          stderr=subprocess.DEVNULL if quiet else None, timeout=300 if quiet else None)


def valid_python(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        result = run([str(path), '-c', 'import sys,venv,ssl,sqlite3,ensurepip; assert sys.version_info >= (3,10)'], quiet=True)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def installed() -> bool:
    interpreter = venv_python()
    if not valid_python(interpreter):
        return False
    if not MARKER.is_file() or MARKER.read_text(encoding='utf-8').strip() != hashlib.sha256(REQ.read_bytes()).hexdigest():
        return False
    try:
        return run([str(interpreter), '-c', IMPORTS], quiet=True).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def missing_modules() -> list[str]:
    """Identify missing SDK/SQL drivers in the project venv without installing anything."""
    interpreter = venv_python()
    if not valid_python(interpreter):
        return list(MODULES.values())
    program = (
        "import importlib.util,json; "
        "names=" + repr(list(MODULES)) + "; "
        "print(json.dumps([name for name in names if importlib.util.find_spec(name) is None]))"
    )
    try:
        result = subprocess.run([str(interpreter), '-c', program], cwd=str(ROOT),
                                check=False, capture_output=True, text=True, timeout=20)
        if result.returncode == 0:
            return [MODULES[name] for name in json.loads(result.stdout.strip())]
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return list(MODULES.values())


def confirm(message: str, *, yes: bool = False) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        raise RuntimeError('Interactive confirmation required; rerun from a terminal or use --yes explicitly.')
    return input(message + ' [y/N]: ').strip().lower() in ('y', 'yes')


def configure() -> None:
    interpreter = venv_python().resolve(strict=True)
    server = (ROOT / 'scripts' / 'mcp_server.py').resolve(strict=True)
    # Higher-precedence project-local override is intentionally ignored by Git.
    # OpenCode replaces the entire MCP server object, so every required field is repeated.
    config = {
        '$schema': 'https://opencode.ai/config.json',
        'mcp': {'servers': {'tohru_db': {
            'type': 'local', 'command': [str(interpreter), str(server)],
            'cwd': str(ROOT.resolve()), 'codemode': False, 'protocol': 'legacy',
            'disabled': False,
        }}}
    }
    LOCAL_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temp = LOCAL_CONFIG.with_suffix('.jsonc.tmp')
    temp.write_text(json.dumps(config, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temp.replace(LOCAL_CONFIG)


def setup(yes: bool = False) -> None:
    if sys.version_info < MIN_PYTHON:
        raise RuntimeError('Python 3.10+ is required by the MCP SDK; run the platform-specific setup wrapper.')
    if not ENV.exists():
        print('[DB Context] Creating isolated .venv ...')
        result = run([sys.executable, '-m', 'venv', str(ENV)])
        if result.returncode != 0:
            raise RuntimeError('Could not create .venv. Install Python venv/ensurepip support and retry.')
    elif not valid_python(venv_python()):
        print('[DB Context] Existing .venv is incompatible with this machine.')
        if not confirm('Replace only the project-local .venv?', yes=yes):
            raise RuntimeError('Setup cancelled; no environment replaced.')
        shutil.rmtree(ENV)
        result = run([sys.executable, '-m', 'venv', str(ENV)])
        if result.returncode != 0:
            raise RuntimeError('Could not rebuild .venv. Check Python venv support.')
    if not installed():
        print('[DB Context] Missing drivers:', ', '.join(missing_modules()) or 'versions have changed; validation needed')
        if not confirm('Install/update the project-local Python dependencies now?', yes=yes):
            raise RuntimeError('Dependency installation declined. Run setup again when ready.')
        result = run([str(venv_python()), '-m', 'pip', 'install', '--disable-pip-version-check',
                      '-r', str(REQ)])
        if result.returncode != 0:
            raise RuntimeError('Package installation failed. Check network, pip, and Python/CPU wheel availability.')
        MARKER.write_text(hashlib.sha256(REQ.read_bytes()).hexdigest(), encoding='utf-8')
        if not installed():
            raise RuntimeError('Driver import verification failed. See pip output and retry.')
    else:
        print('[DB Context] All required Python dependencies are already installed.')
    configure()
    print('[DB Context] Ready. Local MCP configuration written to .opencode/opencode.jsonc (Git-ignored).')
    print('[DB Context] Open this folder in OpenCode and use /connect-db or /export-db.')
    if not shutil.which('opencode'):
        print('[DB Context] Note: OpenCode CLI is not on PATH. You may still use the desktop app.')


def check() -> int:
    print('[DB Context] Python:', sys.version.split()[0], '|', sys.executable)
    print('[DB Context] Project virtual environment:', 'ready' if installed() else 'missing/incomplete')
    missing = missing_modules()
    print('[DB Context] Missing Python modules:', ', '.join(missing) if missing else 'none detected')
    if not missing and not installed():
        print('[DB Context] Module versions/requirements need validation; run setup to repair.')
    print('[DB Context] OpenCode local config:', 'present' if LOCAL_CONFIG.is_file() else 'missing (run setup)')
    print('[DB Context] OpenCode CLI:', shutil.which('opencode') or
          'not found on PATH (the desktop app may still be available)')
    return 0 if installed() and LOCAL_CONFIG.is_file() else 1


def main() -> int:
    parser = argparse.ArgumentParser(description='Prepare or inspect OpenCode JSON DB Context')
    parser.add_argument('--check', action='store_true', help='Report missing project dependencies without changes')
    parser.add_argument('--yes', action='store_true', help='Explicitly approve dependency installation and .venv repair')
    args = parser.parse_args()
    if args.check:
        return check()
    setup(yes=args.yes)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError) as exc:
        print('[DB Context] Setup failed:', exc, file=sys.stderr)
        raise SystemExit(1)
