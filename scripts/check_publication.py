"""Audit files intended for publication, including portability and secret checks.

This is a conservative static check, not a substitute for manual review and
repository secret scanning. It never reads ignored runtime data.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
IGNORED_DIRS = {'.git', '.venv', '.local', 'export', '__pycache__', '.pytest_cache'}
FORBIDDEN_PATHS = re.compile(
    r'(^|/)(?:\.env(?:\..+)?|[^/]+\.(?:sqlite3?|db|pem|p12|pfx|key)|id_(?:rsa|ed25519)[^/]*)$', re.I
)
SECRET_PATTERNS = {
    'Private key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'GitHub token': re.compile(rb'gh[pousr]_[A-Za-z0-9_]{35,}'),
    'AWS key ID': re.compile(rb'AKIA[0-9A-Z]{16}'),
    'OpenAI API key': re.compile(rb'sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{36,}'),
}


def candidate_names() -> list[str]:
    """Keep Git paths lexical: resolving paths can conceal symlinks and collisions."""
    if (ROOT / '.git').exists():
        result = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT,
                                capture_output=True, check=True)
        return [name.decode('utf-8', errors='surrogateescape').replace('\\', '/')
                for name in result.stdout.split(b'\x00') if name]
    return [path.relative_to(ROOT).as_posix() for path in ROOT.rglob('*')
            if path.is_file() and not IGNORED_DIRS.intersection(path.relative_to(ROOT).parts)
            and path.relative_to(ROOT).as_posix() != '.opencode/opencode.jsonc']


def check() -> list[str]:
    findings: list[str] = []
    names = candidate_names()
    folded: dict[str, str] = {}
    for name in names:
        parts = name.split('/')
        if name == '.OpenCode':
            findings.append('Portable path conflict: delete root .OpenCode; keep .opencode/ directory')
        # A file cannot be the parent of another file on case-insensitive filesystems.
        key = name.casefold()
        if key in folded and folded[key] != name:
            findings.append(f'Case-insensitive path collision: {folded[key]} and {name}')
        folded[key] = name
        if FORBIDDEN_PATHS.search(name) and name != '.env.example':
            findings.append(f'Forbidden tracked path: {name}')
        if any(part in IGNORED_DIRS for part in parts):
            findings.append(f'Generated/private directory tracked: {name}')
        path = ROOT / name
        if path.is_symlink():
            findings.append(f'Symlink must be reviewed before publishing: {name}')
            continue
        if not path.is_file():
            findings.append(f'Tracked path is not a regular file: {name}')
            continue
        if path.stat().st_size > 3 * 1024 * 1024:
            findings.append(f'Unusually large source file: {name}')
            continue
        data = path.read_bytes()
        for label, regex in SECRET_PATTERNS.items():
            if regex.search(data) and path.name != 'check_publication.py':
                findings.append(f'Potential {label} in {name}')
    for name in names:
        for parent in Path(name).parents:
            if str(parent) in ('.', ''):
                break
            other = folded.get(parent.as_posix().casefold())
            if other is not None:
                findings.append(f'File/directory name conflict across case-insensitive filesystems: {other} vs {name}')
    return sorted(set(findings))


if __name__ == '__main__':
    errors = check()
    if errors:
        print('\n'.join('[Publication] ' + e for e in errors), file=sys.stderr)
        raise SystemExit(1)
    print('[Publication] No obvious private artifacts, portability conflicts, or credentials detected.')
