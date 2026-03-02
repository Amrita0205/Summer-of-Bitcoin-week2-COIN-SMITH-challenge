#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# web.sh — Coin Smith: PSBT builder web UI and visualizer
#
# Behavior:
#   - Reads PORT env var (default: 3000)
#   - Prints the URL (e.g., http://127.0.0.1:3000) to stdout  ← required
#   - Keeps running until terminated (CTRL+C / SIGTERM)
#   - Serves GET /api/health → 200 { "ok": true }
#   - Serves POST /api/build → accepts fixture JSON, returns report JSON
###############################################################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PORT="${PORT:-3000}"

# Python web server prints the URL itself (to stdout) before starting
exec python3 "$SCRIPT_DIR/web.py"
