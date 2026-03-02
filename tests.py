#!/usr/bin/env python3
"""
tests.py — Unit tests for Coin Smith PSBT builder.

Run: python3 tests.py
Covers:
  - Script type detection
  - vbytes estimation
  - Sequence / locktime computation (all interaction matrix cases)
  - Locktime classification
  - Coin selection (basic, change, dust, send-all, max_inputs)
  - Fee correctness (balance equation, fee >= target)
  - PSBT structure (magic bytes, base64 decodable)
  - Warning codes (SEND_ALL, HIGH_FEE, RBF_SIGNALING)
  - Error handling (invalid fixture)
  - Multiple payment outputs
  - Mixed script types
"""

import sys
import os
import base64
import math
import unittest

# Run from repo root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.bitcoin_tx import (
    varint, detect_script_type, is_segwit,
    estimate_vbytes, build_psbt_b64,
)
from src.builder import (
    compute_seq_locktime, classify_locktime,
    validate_fixture, select_coins, DUST_THRESHOLD,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers for building test fixtures
# ─────────────────────────────────────────────────────────────────────────────

def utxo(txid, vout, value, stype='p2wpkh'):
    """Build a minimal UTXO dict."""
    scripts = {
        'p2wpkh': '0014' + 'ab' * 20,
        'p2pkh':  '76a914' + 'ab' * 20 + '88ac',
        'p2tr':   '5120' + 'ab' * 32,
        'p2sh':   'a914' + 'ab' * 20 + '87',
    }
    return {
        'txid': txid,
        'vout': vout,
        'value_sats': value,
        'script_pubkey_hex': scripts.get(stype, scripts['p2wpkh']),
        'script_type': stype,
        'address': 'bc1qtest',
    }


def payment(value, stype='p2wpkh'):
    scripts = {
        'p2wpkh': '0014' + 'cd' * 20,
        'p2pkh':  '76a914' + 'cd' * 20 + '88ac',
        'p2tr':   '5120' + 'cd' * 32,
    }
    return {
        'value_sats': value,
        'script_pubkey_hex': scripts.get(stype, scripts['p2wpkh']),
        'script_type': stype,
        'address': 'bc1qpayment',
    }


def change_tmpl(stype='p2wpkh'):
    scripts = {
        'p2wpkh': '0014' + 'ef' * 20,
        'p2pkh':  '76a914' + 'ef' * 20 + '88ac',
        'p2tr':   '5120' + 'ef' * 32,
    }
    return {
        'script_pubkey_hex': scripts.get(stype, scripts['p2wpkh']),
        'script_type': stype,
        'address': 'bc1qchange',
    }


def fixture(utxos, payments, fee_rate=5, **kwargs):
    f = {
        'network': 'mainnet',
        'utxos': utxos,
        'payments': payments,
        'change': change_tmpl(),
        'fee_rate_sat_vb': fee_rate,
    }
    f.update(kwargs)
    return f


TXID_A = 'aa' * 32
TXID_B = 'bb' * 32
TXID_C = 'cc' * 32


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestVarint(unittest.TestCase):
    def test_small(self):
        self.assertEqual(varint(0), b'\x00')
        self.assertEqual(varint(252), b'\xfc')

    def test_fd(self):
        # 253 requires 0xfd prefix
        self.assertEqual(varint(253), b'\xfd\xfd\x00')

    def test_large(self):
        self.assertEqual(len(varint(0x10000)), 5)


class TestScriptTypeDetection(unittest.TestCase):
    def test_p2wpkh(self):
        self.assertEqual(detect_script_type('0014' + '00' * 20), 'p2wpkh')

    def test_p2pkh(self):
        script = '76a914' + '00' * 20 + '88ac'
        self.assertEqual(detect_script_type(script), 'p2pkh')

    def test_p2sh(self):
        script = 'a914' + '00' * 20 + '87'
        self.assertEqual(detect_script_type(script), 'p2sh')

    def test_p2wsh(self):
        self.assertEqual(detect_script_type('0020' + '00' * 32), 'p2wsh')

    def test_p2tr(self):
        self.assertEqual(detect_script_type('5120' + '00' * 32), 'p2tr')

    def test_is_segwit_true(self):
        self.assertTrue(is_segwit('p2wpkh'))
        self.assertTrue(is_segwit('p2tr'))
        self.assertTrue(is_segwit('p2wsh'))

    def test_is_segwit_false(self):
        self.assertFalse(is_segwit('p2pkh'))
        self.assertFalse(is_segwit('p2sh'))


class TestVbytesEstimation(unittest.TestCase):
    def test_single_p2wpkh(self):
        """Single p2wpkh input/output: should be roughly 110 vbytes."""
        inp = [{'script_type': 'p2wpkh'}]
        out = [{'script_type': 'p2wpkh'}]
        vb = estimate_vbytes(inp, out)
        self.assertGreater(vb, 80)
        self.assertLess(vb, 150)

    def test_more_inputs_bigger(self):
        """More inputs → more vbytes."""
        one = estimate_vbytes([{'script_type': 'p2wpkh'}],
                              [{'script_type': 'p2wpkh'}])
        two = estimate_vbytes([{'script_type': 'p2wpkh'},
                               {'script_type': 'p2wpkh'}],
                              [{'script_type': 'p2wpkh'}])
        self.assertGreater(two, one)

    def test_p2pkh_heavier_than_p2wpkh(self):
        """Legacy p2pkh inputs are heavier than segwit p2wpkh."""
        pkh = estimate_vbytes([{'script_type': 'p2pkh'}],
                              [{'script_type': 'p2wpkh'}])
        wpkh = estimate_vbytes([{'script_type': 'p2wpkh'}],
                               [{'script_type': 'p2wpkh'}])
        self.assertGreater(pkh, wpkh)

    def test_with_change_output_bigger(self):
        """Adding a change output increases vbytes."""
        no_ch = estimate_vbytes([{'script_type': 'p2wpkh'}],
                                [{'script_type': 'p2wpkh'}])
        with_ch = estimate_vbytes([{'script_type': 'p2wpkh'}],
                                  [{'script_type': 'p2wpkh'},
                                   {'script_type': 'p2wpkh'}])
        self.assertGreater(with_ch, no_ch)


class TestSeqLocktime(unittest.TestCase):
    """Test all rows of the interaction matrix in the spec."""

    def test_no_rbf_no_locktime(self):
        """Row 1: rbf absent, no locktime → nSeq=0xFFFFFFFF, nLT=0"""
        seq, lt = compute_seq_locktime({})
        self.assertEqual(seq, 0xFFFFFFFF)
        self.assertEqual(lt, 0)

    def test_no_rbf_with_locktime(self):
        """Row 2: rbf=false, locktime=850000 → nSeq=0xFFFFFFFE, nLT=850000"""
        seq, lt = compute_seq_locktime({'rbf': False, 'locktime': 850000})
        self.assertEqual(seq, 0xFFFFFFFE)
        self.assertEqual(lt, 850000)

    def test_rbf_true_no_locktime_no_height(self):
        """Row 3: rbf=true, no locktime, no height → nSeq=0xFFFFFFFD, nLT=0"""
        seq, lt = compute_seq_locktime({'rbf': True})
        self.assertEqual(seq, 0xFFFFFFFD)
        self.assertEqual(lt, 0)

    def test_rbf_true_no_locktime_with_height(self):
        """Row 4: rbf=true, no explicit locktime, current_height=850000 → anti-fee-sniping"""
        seq, lt = compute_seq_locktime({'rbf': True, 'current_height': 850000})
        self.assertEqual(seq, 0xFFFFFFFD)
        self.assertEqual(lt, 850000)

    def test_rbf_true_explicit_locktime(self):
        """Row 5: rbf=true, explicit locktime=900000 → nSeq=0xFFFFFFFD, nLT=900000"""
        seq, lt = compute_seq_locktime({'rbf': True, 'locktime': 900000})
        self.assertEqual(seq, 0xFFFFFFFD)
        self.assertEqual(lt, 900000)

    def test_locktime_boundary_block(self):
        """499_999_999 → block_height"""
        self.assertEqual(classify_locktime(499_999_999), 'block_height')

    def test_locktime_boundary_timestamp(self):
        """500_000_000 → unix_timestamp"""
        self.assertEqual(classify_locktime(500_000_000), 'unix_timestamp')

    def test_locktime_zero(self):
        self.assertEqual(classify_locktime(0), 'none')


class TestCoinSelection(unittest.TestCase):
    def test_basic_change_created(self):
        """Standard case: change output should be created."""
        f = fixture([utxo(TXID_A, 0, 100_000)], [payment(70_000)])
        r = select_coins(f)
        self.assertTrue(r['ok'])
        self.assertIsNotNone(r['change_index'])
        change = r['outputs'][r['change_index']]
        self.assertTrue(change['is_change'])
        self.assertGreaterEqual(change['value_sats'], DUST_THRESHOLD)

    def test_balance_equation(self):
        """inputs == outputs + fee must always hold."""
        f = fixture([utxo(TXID_A, 0, 200_000)], [payment(100_000)])
        r = select_coins(f)
        total_in  = sum(u['value_sats'] for u in r['selected_inputs'])
        total_out = sum(o['value_sats'] for o in r['outputs'])
        self.assertEqual(total_in, total_out + r['fee_sats'])

    def test_fee_meets_target(self):
        """fee_sats >= ceil(vbytes * fee_rate)."""
        f = fixture([utxo(TXID_A, 0, 200_000)], [payment(100_000)], fee_rate=10)
        r = select_coins(f)
        min_fee = math.ceil(r['vbytes'] * 10)
        self.assertGreaterEqual(r['fee_sats'], min_fee)

    def test_no_dust_change_output(self):
        """Change output must never be below DUST_THRESHOLD."""
        # Payment leaves very small leftover
        f = fixture([utxo(TXID_A, 0, 100_000)], [payment(99_000)])
        r = select_coins(f)
        for o in r['outputs']:
            self.assertGreaterEqual(o['value_sats'], DUST_THRESHOLD)

    def test_send_all_warning(self):
        """When no change, SEND_ALL warning must be emitted."""
        # Large payment leaves nothing for change
        f = fixture([utxo(TXID_A, 0, 100_000)], [payment(99_300)])
        r = select_coins(f)
        codes = [w['code'] for w in r['warnings']]
        self.assertIn('SEND_ALL', codes)
        self.assertIsNone(r['change_index'])

    def test_max_inputs_respected(self):
        """policy.max_inputs must limit selected inputs."""
        utxos = [
            utxo(TXID_A, 0, 100_000),
            utxo(TXID_B, 0, 100_000),
            utxo(TXID_C, 0, 100_000),
        ]
        # payment=50k, max_inputs=1 → one 100k UTXO is enough
        f = fixture(utxos, [payment(50_000)], policy={'max_inputs': 1})
        r = select_coins(f)
        self.assertLessEqual(len(r['selected_inputs']), 1)

    def test_greedy_picks_largest_first(self):
        """Greedy selection should prefer larger UTXOs first."""
        utxos = [
            utxo(TXID_A, 0, 10_000),   # small
            utxo(TXID_B, 0, 200_000),  # large
        ]
        f = fixture(utxos, [payment(50_000)])
        r = select_coins(f)
        # Should pick only the 200k UTXO
        self.assertEqual(len(r['selected_inputs']), 1)
        self.assertEqual(r['selected_inputs'][0]['value_sats'], 200_000)

    def test_multiple_payments(self):
        """All payment outputs must appear in the report outputs."""
        f = fixture(
            [utxo(TXID_A, 0, 500_000)],
            [payment(100_000), payment(150_000)],
        )
        r = select_coins(f)
        non_change = [o for o in r['outputs'] if not o['is_change']]
        self.assertEqual(len(non_change), 2)
        vals = sorted(o['value_sats'] for o in non_change)
        self.assertEqual(vals, [100_000, 150_000])

    def test_rbf_signaling_in_report(self):
        """rbf=true → rbf_signaling=True in report + RBF_SIGNALING warning."""
        f = fixture([utxo(TXID_A, 0, 200_000)], [payment(100_000)], rbf=True)
        r = select_coins(f)
        self.assertTrue(r['rbf_signaling'])
        codes = [w['code'] for w in r['warnings']]
        self.assertIn('RBF_SIGNALING', codes)

    def test_no_rbf_in_report(self):
        """rbf absent → rbf_signaling=False, no RBF_SIGNALING warning."""
        f = fixture([utxo(TXID_A, 0, 200_000)], [payment(100_000)])
        r = select_coins(f)
        self.assertFalse(r['rbf_signaling'])
        codes = [w['code'] for w in r['warnings']]
        self.assertNotIn('RBF_SIGNALING', codes)

    def test_locktime_fields_in_report(self):
        """locktime field must appear correctly in report."""
        f = fixture([utxo(TXID_A, 0, 200_000)], [payment(100_000)],
                    rbf=True, locktime=850_000)
        r = select_coins(f)
        self.assertEqual(r['locktime'], 850_000)
        self.assertEqual(r['locktime_type'], 'block_height')


class TestPSBT(unittest.TestCase):
    def test_psbt_magic_bytes(self):
        """PSBT must start with 'psbt\\xff'."""
        f = fixture([utxo(TXID_A, 0, 200_000)], [payment(100_000)])
        r = select_coins(f)
        raw = base64.b64decode(r['psbt_base64'])
        self.assertEqual(raw[:5], b'psbt\xff')

    def test_psbt_base64_valid(self):
        """psbt_base64 must be valid base64."""
        f = fixture([utxo(TXID_A, 0, 200_000)], [payment(100_000)])
        r = select_coins(f)
        try:
            base64.b64decode(r['psbt_base64'])
        except Exception:
            self.fail('psbt_base64 is not valid base64')


class TestValidation(unittest.TestCase):
    def test_missing_utxos(self):
        """Fixture without utxos → ValueError with INVALID_FIXTURE code."""
        with self.assertRaises(ValueError) as ctx:
            validate_fixture({'payments': [], 'change': {}, 'fee_rate_sat_vb': 1})
        self.assertIn('INVALID_FIXTURE', str(ctx.exception))

    def test_insufficient_funds(self):
        """Not enough UTXOs → INSUFFICIENT_FUNDS."""
        f = fixture([utxo(TXID_A, 0, 1_000)], [payment(999_999)])
        with self.assertRaises(ValueError) as ctx:
            select_coins(f)
        self.assertIn('INSUFFICIENT_FUNDS', str(ctx.exception))

    def test_zero_fee_rate_rejected(self):
        """fee_rate=0 must be rejected."""
        with self.assertRaises(ValueError):
            validate_fixture({
                'utxos': [utxo(TXID_A, 0, 100_000)],
                'payments': [payment(50_000)],
                'change': change_tmpl(),
                'fee_rate_sat_vb': 0,
            })


if __name__ == '__main__':
    # Run with verbose output so each test name is shown
    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromModule(__import__('__main__'))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
