#!/usr/bin/env bash
# One-shot local setup for Vista (engine + desktop recorder).
#   ./setup.sh          install + run both test suites
#   ./setup.sh --check  only report what is installed / missing
set -euo pipefail
cd "$(dirname "$0")"

MIN_NODE=18
MIN_PY="3.12"
CHECK_ONLY=${1:-}
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*"; MISSING=1; }
MISSING=0

echo "Checking prerequisites"

# --- OS ------------------------------------------------------------------------
OS=$(uname -s)
case $OS in
  Darwin) ok "macOS $(sw_vers -productVersion 2>/dev/null || echo '?') ($(uname -m))" ;;
  Linux)  ok "Linux ($(uname -m)) - recorder needs an X11/Wayland display" ;;
  *)      warn "$OS - untested; use WSL or a Mac for the recorder" ;;
esac

# --- uv / Python -----------------------------------------------------------------
if command -v uv >/dev/null; then
  ok "uv $(uv --version | awk '{print $2}')"
else
  fail "uv not found - install with:  curl -LsSf https://astral.sh/uv/install.sh | sh   (or: brew install uv)"
fi
if command -v uv >/dev/null && PY=$(uv python find ">=$MIN_PY" 2>/dev/null); then
  ok "Python $("$PY" -c 'import sys;print(".".join(map(str,sys.version_info[:3])))') ($PY)"
elif command -v uv >/dev/null; then
  warn "no Python >= $MIN_PY on this machine - uv will download one during sync"
fi

# --- Node / npm ----------------------------------------------------------------
if command -v node >/dev/null; then
  NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]')
  if [ "$NODE_MAJOR" -ge $MIN_NODE ]; then ok "Node $(node -v), npm $(npm -v)"
  else fail "Node $(node -v) is too old - need >= $MIN_NODE (brew install node)"; fi
else
  fail "Node not found - install with:  brew install node   (or https://nodejs.org, >= $MIN_NODE)"
fi

# --- Electron (installed by npm install; report if already there) ---------------
if [ -x src/recorder/node_modules/.bin/electron ]; then
  ok "Electron $(src/recorder/node_modules/.bin/electron --version 2>/dev/null | tr -d v)"
else
  warn "Electron not installed yet - npm install will fetch it (~100 MB)"
fi

if [ "$MISSING" -ne 0 ]; then
  echo; echo "Install the missing tools above and re-run ./setup.sh"; exit 1
fi
[ "$CHECK_ONLY" = "--check" ] && exit 0

# --- Install ---------------------------------------------------------------------
echo; echo "Installing Python engine (uv sync)"
uv sync --group dev

echo; echo "Installing recorder (npm install)"
(cd src/recorder && npm install --no-fund --no-audit)

# --- Test ----------------------------------------------------------------------
echo; echo "Running tests"
uv run ruff check . && uv run ruff format --check .
uv run pytest -q tests/test_pipeline.py
(cd src/recorder && npm test)
uv run python -m taskmining run --synthetic 5 --out /tmp/vista-setup-check >/dev/null && ok "taskmining CLI"

# --- Next steps ----------------------------------------------------------------
cat <<EOF

Done. Next:

  make demo     recorder with simulated Outlook/Acrobat/QuickBooks/Excel activity
  make start    real recording (hooks + screenshots + video)

EOF
if [ "$OS" = Darwin ]; then
cat <<EOF
macOS: the first Start asks for three permissions in System Settings > Privacy & Security
(the app appears there as "Electron" in development):
  Accessibility, Input Monitoring, Screen Recording
Grant them, then press Start again. Recordings land in ~/Vista/recordings/<id>/.

EOF
fi
