#!/bin/bash
# Kent BRRR Scout — macOS / Linux launcher. Double-click (Mac) or run ./start.sh (Linux).
# First run sets everything up (2–5 minutes); after that it opens in a few seconds.
set -e
cd "$(dirname "$0")"

PY=""
for c in python3.13 python3.12 python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.12 or newer is needed. Install it from https://www.python.org/downloads/ and run this again."
  read -r -p "Press Enter to close…"; exit 1
fi

if [ ! -d .venv ]; then
  echo "First run: creating a private Python environment…"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

STAMP=".venv/.installed-$(shasum pyproject.toml 2>/dev/null | cut -c1-12 || md5sum pyproject.toml | cut -c1-12)"
if [ ! -f "$STAMP" ]; then
  echo "Installing / updating components…"
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -e .
  if [ ! -d "/Applications/Google Chrome.app" ] && ! command -v google-chrome >/dev/null 2>&1; then
    echo "Google Chrome not found — downloading a browser for the portal collector…"
    python -m playwright install chromium || echo "Browser download failed — install Google Chrome, then run this again."
  fi
  rm -f .venv/.installed-*; touch "$STAMP"
fi

[ -f .env ] || cp .env.example .env

echo ""
echo "Kent BRRR Scout is running at http://127.0.0.1:8000  (this window must stay open; close it to stop)"
python -m src.web --open
