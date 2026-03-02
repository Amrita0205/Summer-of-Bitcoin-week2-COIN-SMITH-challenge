#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# setup.sh — Install dependencies for Coin Smith
#
# Only external dependency: Flask (web server).
# All Bitcoin logic is pure Python stdlib.
###############################################################################

echo "Installing dependencies..."

# Try pip3 first, fall back to pip
PIP="pip3"
if ! command -v pip3 &>/dev/null; then
  PIP="pip"
fi

# Install Flask (needed for web.py)
$PIP install flask --quiet --break-system-packages 2>/dev/null \
  || $PIP install flask --quiet \
  || true  # Already installed is fine

echo "Setup complete"
