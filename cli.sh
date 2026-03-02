#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# cli.sh — Coin Smith: PSBT transaction builder CLI
#
# Usage:
#   ./cli.sh <fixture.json>
#
# Workflow:
#   1. Read the fixture JSON (UTXOs, payments, change template, fee rate)
#   2. Select coins (inputs) to fund the payments
#   3. Compute fee, change, and construct outputs
#   4. Build an unsigned PSBT (BIP-174)
#   5. Write JSON report to out/<fixture_name>.json
#   6. Exit 0 on success, 1 on error
#
# On error, writes { "ok": false, "error": { "code": "...", "message": "..." } }
# to the output file and exits 1.
###############################################################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -lt 1 ]]; then
  echo "Error: no fixture file provided" >&2
  echo "Usage: ./cli.sh <fixture.json>" >&2
  exit 1
fi

FIXTURE="$1"

# Derive output filename:  fixtures/basic_change_p2wpkh.json → out/basic_change_p2wpkh.json
FIXTURE_NAME="$(basename "$FIXTURE")"
mkdir -p "$SCRIPT_DIR/out"

# Run Python builder — all logs go to stderr, report written by cli.py
python3 "$SCRIPT_DIR/cli.py" "$FIXTURE"
EXIT_CODE=$?

exit $EXIT_CODE
