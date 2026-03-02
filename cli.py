#!/usr/bin/env python3
"""
cli.py — Command-line entry point for Coin Smith PSBT builder.

Usage:  python3 cli.py <fixture.json>

Output: writes JSON report to out/<fixture_basename>.json
Logs:   anything informational goes to stderr
Exit:   0 on success, 1 on error
"""

import sys
import os
import json
import traceback

# So we can do `from src.builder import ...` when run from repo root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.builder import validate_fixture, select_coins


def write_error(out_path: str, code: str, message: str) -> None:
    """Write a structured error JSON to the output file."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump({'ok': False, 'error': {'code': code, 'message': message}}, f, indent=2)


def main() -> int:
    if len(sys.argv) < 2:
        print('Usage: cli.py <fixture.json>', file=sys.stderr)
        return 1

    fixture_path = sys.argv[1]

    # Derive output path:  fixtures/basic_change_p2wpkh.json → out/basic_change_p2wpkh.json
    fixture_name = os.path.basename(fixture_path)  # keep .json extension
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')
    out_path = os.path.join(out_dir, fixture_name)
    os.makedirs(out_dir, exist_ok=True)

    # ── Read fixture ──────────────────────────────────────────────────────────
    try:
        with open(fixture_path, 'r') as f:
            fixture = json.load(f)
    except FileNotFoundError:
        write_error(out_path, 'FILE_NOT_FOUND', f'Fixture not found: {fixture_path}')
        print(f'Error: fixture not found: {fixture_path}', file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        write_error(out_path, 'INVALID_FIXTURE', f'JSON parse error: {e}')
        print(f'Error: invalid JSON: {e}', file=sys.stderr)
        return 1

    # ── Validate fixture ──────────────────────────────────────────────────────
    try:
        validate_fixture(fixture)
    except ValueError as e:
        msg = str(e)
        # Our errors are formatted as "CODE: message"
        if ': ' in msg:
            code, message = msg.split(': ', 1)
        else:
            code, message = 'INVALID_FIXTURE', msg
        write_error(out_path, code, message)
        print(f'Validation error: {msg}', file=sys.stderr)
        return 1

    # ── Build transaction ─────────────────────────────────────────────────────
    try:
        report = select_coins(fixture)
    except ValueError as e:
        msg = str(e)
        if ': ' in msg:
            code, message = msg.split(': ', 1)
        else:
            code, message = 'BUILD_ERROR', msg
        write_error(out_path, code, message)
        print(f'Build error: {msg}', file=sys.stderr)
        return 1
    except Exception as e:
        write_error(out_path, 'INTERNAL_ERROR', str(e))
        traceback.print_exc(file=sys.stderr)
        return 1

    # ── Write report ──────────────────────────────────────────────────────────
    with open(out_path, 'w') as f:
        json.dump(report, f, indent=2)
    print(f'Report written to {out_path}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
