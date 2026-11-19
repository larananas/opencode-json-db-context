#!/bin/sh
# Portable macOS/Linux bootstrap; no architecture-specific binaries or Rosetta.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"

works() {
  "$1" -c 'import sys,venv,ssl,sqlite3,ensurepip; assert sys.version_info >= (3,10)' >/dev/null 2>&1
}

find_python() {
  if [ -n "${TOHRU_DB_PYTHON:-}" ] && works "$TOHRU_DB_PYTHON"; then
    printf '%s\n' "$TOHRU_DB_PYTHON"; return 0
  fi
  for name in python3 python3.14 python3.13 python3.12 python3.11 python3.10 python; do
    command -v "$name" >/dev/null 2>&1 || continue
    path=$(command -v "$name")
    if works "$path"; then printf '%s\n' "$path"; return 0; fi
  done
  for prefix in /opt/homebrew/bin /usr/local/bin /usr/bin /opt/local/bin; do
    for name in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
      path="$prefix/$name"
      if [ -x "$path" ] && works "$path"; then printf '%s\n' "$path"; return 0; fi
    done
  done
  return 1
}

if [ "${1:-}" = '--check' ]; then
  if PYTHON=$(find_python); then
    "$PYTHON" scripts/setup.py --check
  else
    echo '[DB Context] Missing: Python 3.10+ with venv, ensurepip, ssl, sqlite3.' >&2
    exit 1
  fi
  exit $?
fi

if ! PYTHON=$(find_python); then
  echo '[DB Context] Missing: compatible Python 3.10+ (including venv and pip bootstrap).'
  echo '[DB Context] No Python installation will happen without your approval.'
  printf 'Try installing Python using this system package manager? [y/N]: '
  if [ -r /dev/tty ]; then IFS= read -r answer </dev/tty; else IFS= read -r answer || answer=''; fi
  case "$answer" in y|Y|yes|YES) ;; *) echo '[DB Context] Installation skipped. Install Python 3.10+ and run setup again.'; exit 1 ;; esac

  if [ "$(uname -s)" = 'Darwin' ]; then
    if command -v brew >/dev/null 2>&1; then
      brew install python@3.13
    else
      echo '[DB Context] Homebrew unavailable. Install Python 3.10+ from https://www.python.org/downloads/ and rerun setup.' >&2
      exit 1
    fi
  elif command -v apt-get >/dev/null 2>&1; then
    echo '[DB Context] Debian/Ubuntu: installing python3, python3-venv, python3-pip.'
    if [ "$(id -u)" -eq 0 ]; then apt-get update && apt-get install -y python3 python3-venv python3-pip
    elif command -v sudo >/dev/null 2>&1; then sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip
    else echo '[DB Context] Administrator privileges are required.' >&2; exit 1; fi
  elif command -v dnf >/dev/null 2>&1; then
    if [ "$(id -u)" -eq 0 ]; then dnf install -y python3 python3-pip
    elif command -v sudo >/dev/null 2>&1; then sudo dnf install -y python3 python3-pip
    else echo '[DB Context] Administrator privileges are required.' >&2; exit 1; fi
  elif command -v pacman >/dev/null 2>&1; then
    if [ "$(id -u)" -eq 0 ]; then pacman -S --noconfirm --needed python python-pip
    elif command -v sudo >/dev/null 2>&1; then sudo pacman -S --noconfirm --needed python python-pip
    else echo '[DB Context] Administrator privileges are required.' >&2; exit 1; fi
  elif command -v zypper >/dev/null 2>&1; then
    if [ "$(id -u)" -eq 0 ]; then zypper --non-interactive install python3 python3-pip
    elif command -v sudo >/dev/null 2>&1; then sudo zypper --non-interactive install python3 python3-pip
    else echo '[DB Context] Administrator privileges are required.' >&2; exit 1; fi
  elif command -v apk >/dev/null 2>&1; then
    if [ "$(id -u)" -eq 0 ]; then apk add python3 py3-pip py3-virtualenv
    elif command -v sudo >/dev/null 2>&1; then sudo apk add python3 py3-pip py3-virtualenv
    else echo '[DB Context] Administrator privileges are required.' >&2; exit 1; fi
  else
    echo '[DB Context] No supported package manager detected. Install Python 3.10+ from your OS package source or https://www.python.org/downloads/.' >&2
    exit 1
  fi
  if ! PYTHON=$(find_python); then
    echo '[DB Context] Python is still unavailable or lacks venv/ensurepip. Fix the system installation, then rerun setup.' >&2
    exit 1
  fi
fi
"$PYTHON" scripts/setup.py "$@"
