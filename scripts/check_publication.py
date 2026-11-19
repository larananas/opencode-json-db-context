"""Fail releases containing local artifacts or obvious credentials.

This check is deliberately conservative: passing does not prove the absence
of every possible secret. Pair it with review and repository secret scanning.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
IGNORED_DIRS = {'.git', '.venv', '.local', 'export', '__pycache__', '.pytest_cache'}
FORBIDDEN_PATHS = re.compile(r'(^|/)(?:\.env(?:\..+)?|[^/]+\.(?:sqlite3?|db|pem|p12|pfx|key)|id_(?:rsa|ed25519)[^/]*)$', re.I)
SECRET_PATTERNS = {
    'Private key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'GitHub token': re.compile(rb'gh[pousr]_[A-Za-z0-9_]{35,}'),
    'AWS key ID': re.compile(rb'AKIA[0-9A-Z]{16}'),
    'OpenAI API key': re.compile(rb'sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{36,}'),
}


def candidate_files() -> list[Path]:
    if (ROOT / '.git').exists():
        result = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=True)
        return [(ROOT / name.decode()).resolve() for name in result.stdout.split(b'\x00') if name]
    return [p for p in ROOT.rglob('*') if p.is_file() and not IGNORED_DIRS.intersection(p.relative_to(ROOT).parts)
            and p.relative_to(ROOT).as_posix() != '.opencode/opencode.jsonc']


def check() -> list[str]:
    findings: list[str] = []
    for path in candidate_files():
        name = path.relative_to(ROOT).as_posix()
        if FORBIDDEN_PATHS.search(name) and name != '.env.example':
            findings.append(f'Forbidden tracked path: {name}')
        if any(part in IGNORED_DIRS for part in path.relative_to(ROOT).parts):
            findings.append(f'Generated/private directory tracked: {name}')
        if path.is_symlink():
            findings.append(f'Symlink must be reviewed before publishing: {name}')
            continue
        if path.stat().st_size > 3 * 1024 * 1024:
            findings.append(f'Unusually large source file: {name}')
            continue
        data = path.read_bytes()
        for label, regex in SECRET_PATTERNS.items():
            if regex.search(data) and path.name != 'check_publication.py':
                findings.append(f'Potential {label} in {name}')
    return findings


if __name__ == '__main__':
    errors = check()
    if errors:
        print('\n'.join('[Publication] ' + e for e in errors), file=sys.stderr)
        raise SystemExit(1)
    print('[Publication] No obvious credentials, symlinks, or private artifacts detected in publishable files.')
