"""
builder.py — Coin selection, fee calculation, PSBT report generation.

The flow:
  1. validate_fixture()     — reject bad inputs early with structured errors
  2. compute_seq_locktime() — figure out nSequence and nLockTime from rbf/locktime fields
  3. select_coins()         — greedy coin selection, handles the change/fee boundary problem
  4. build_report()         — assemble the final JSON report dict
"""

import math
from .bitcoin_tx import (
    detect_script_type,
    is_segwit,
    estimate_vbytes,
    build_psbt_b64,
)

DUST_THRESHOLD = 546  # satoshis — outputs below this are "dust" and must not be created


# ─────────────────────────────────────────────────────────────────────────────
# RBF / Locktime logic
#
# From the spec interaction matrix:
#
#   rbf=false, no locktime         → nSeq=0xFFFFFFFF, nLockTime=0
#   rbf=false, locktime present    → nSeq=0xFFFFFFFE, nLockTime=locktime
#   rbf=true,  no locktime, no height → nSeq=0xFFFFFFFD, nLockTime=0
#   rbf=true,  no locktime, height present → nSeq=0xFFFFFFFD, nLockTime=height
#   rbf=true,  locktime present    → nSeq=0xFFFFFFFD, nLockTime=locktime
# ─────────────────────────────────────────────────────────────────────────────

def compute_seq_locktime(fixture: dict) -> tuple:
    """
    Returns (nsequence, nlocktime) based on fixture rbf/locktime/current_height.

    nLockTime rules (in priority order):
      1. If 'locktime' key is present → use that value
      2. Else if rbf=true AND current_height present → use current_height (anti-fee-sniping)
      3. Otherwise → 0

    nSequence rules:
      - rbf=true  → 0xFFFFFFFD  (signals RBF per BIP-125)
      - rbf=false but nlocktime != 0 → 0xFFFFFFFE  (enables locktime, no RBF signal)
      - otherwise → 0xFFFFFFFF  (final, no RBF, no locktime)
    """
    rbf = fixture.get('rbf', False)
    locktime_field = fixture.get('locktime', None)     # explicit locktime value
    current_height = fixture.get('current_height', None)

    # Determine nLockTime
    if locktime_field is not None:
        nlocktime = locktime_field
    elif rbf and current_height is not None:
        nlocktime = current_height  # Bitcoin Core anti-fee-sniping behaviour
    else:
        nlocktime = 0

    # Determine nSequence
    if rbf:
        nsequence = 0xFFFFFFFD
    elif nlocktime != 0:
        nsequence = 0xFFFFFFFE
    else:
        nsequence = 0xFFFFFFFF

    return nsequence, nlocktime


def classify_locktime(nlocktime: int) -> str:
    """
    Return locktime_type string from the final nLockTime value.
      0               → "none"
      1..499_999_999  → "block_height"
      500_000_000+    → "unix_timestamp"
    """
    if nlocktime == 0:
        return "none"
    elif nlocktime < 500_000_000:
        return "block_height"
    else:
        return "unix_timestamp"


# ─────────────────────────────────────────────────────────────────────────────
# Fixture validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_fixture(fixture: dict) -> None:
    """
    Raise ValueError("CODE: message") if fixture is malformed.
    We check only what we need — extra fields are silently ignored per spec.
    """
    if not isinstance(fixture, dict):
        raise ValueError("INVALID_FIXTURE: fixture must be a JSON object")

    # Required top-level keys
    for key in ('utxos', 'payments', 'change', 'fee_rate_sat_vb'):
        if key not in fixture:
            raise ValueError(f"INVALID_FIXTURE: missing required field '{key}'")

    utxos = fixture['utxos']
    payments = fixture['payments']
    change = fixture['change']
    fee_rate = fixture['fee_rate_sat_vb']

    if not isinstance(utxos, list) or len(utxos) == 0:
        raise ValueError("INVALID_FIXTURE: 'utxos' must be a non-empty array")

    if not isinstance(payments, list) or len(payments) == 0:
        raise ValueError("INVALID_FIXTURE: 'payments' must be a non-empty array")

    if not isinstance(change, dict):
        raise ValueError("INVALID_FIXTURE: 'change' must be an object")

    if 'script_pubkey_hex' not in change:
        raise ValueError("INVALID_FIXTURE: 'change' must have 'script_pubkey_hex'")

    try:
        fee_rate = float(fee_rate)
    except (TypeError, ValueError):
        raise ValueError("INVALID_FIXTURE: 'fee_rate_sat_vb' must be a number")

    if fee_rate <= 0:
        raise ValueError("INVALID_FIXTURE: 'fee_rate_sat_vb' must be positive")

    # Validate each UTXO
    for i, u in enumerate(utxos):
        for field in ('txid', 'vout', 'value_sats', 'script_pubkey_hex'):
            if field not in u:
                raise ValueError(f"INVALID_FIXTURE: utxo[{i}] missing field '{field}'")
        if not isinstance(u['txid'], str) or len(u['txid']) != 64:
            raise ValueError(f"INVALID_FIXTURE: utxo[{i}].txid must be 64-char hex string")
        try:
            bytes.fromhex(u['txid'])
        except ValueError:
            raise ValueError(f"INVALID_FIXTURE: utxo[{i}].txid is not valid hex")
        if not isinstance(u['value_sats'], int) or u['value_sats'] <= 0:
            raise ValueError(f"INVALID_FIXTURE: utxo[{i}].value_sats must be positive integer")
        if not isinstance(u['vout'], int) or u['vout'] < 0:
            raise ValueError(f"INVALID_FIXTURE: utxo[{i}].vout must be non-negative integer")

    # Validate each payment
    for i, p in enumerate(payments):
        for field in ('script_pubkey_hex', 'value_sats'):
            if field not in p:
                raise ValueError(f"INVALID_FIXTURE: payment[{i}] missing field '{field}'")
        if not isinstance(p['value_sats'], int) or p['value_sats'] <= 0:
            raise ValueError(f"INVALID_FIXTURE: payment[{i}].value_sats must be positive integer")


# ─────────────────────────────────────────────────────────────────────────────
# Coin selection
#
# Strategy: Greedy — sort UTXOs largest-first, add them one by one until we
# have enough to cover (payment_total + minimum_fee).
#
# The tricky part: whether we include a change output or not affects the fee,
# because the tx is bigger with a change output.
# We handle the boundary correctly by checking both cases each iteration:
#   Case A: with change output → leftover must be >= DUST_THRESHOLD (546 sats)
#   Case B: without change     → leftover goes entirely to miner as fee
# ─────────────────────────────────────────────────────────────────────────────

def select_coins(fixture: dict) -> dict:
    """
    Main entry point. Given a validated fixture dict, run coin selection
    and return the full report dict.
    """
    utxos       = fixture['utxos']
    payments    = fixture['payments']
    change_tmpl = fixture['change']
    fee_rate    = float(fixture['fee_rate_sat_vb'])
    network     = fixture.get('network', 'mainnet')
    policy      = fixture.get('policy', {})
    max_inputs  = policy.get('max_inputs', None)  # None = unlimited

    # Fill in script_type where missing (use script_pubkey_hex as authoritative)
    for u in utxos:
        if not u.get('script_type'):
            u['script_type'] = detect_script_type(u['script_pubkey_hex'])
    for p in payments:
        if not p.get('script_type'):
            p['script_type'] = detect_script_type(p['script_pubkey_hex'])
    if not change_tmpl.get('script_type'):
        change_tmpl['script_type'] = detect_script_type(change_tmpl['script_pubkey_hex'])

    # payment_total = sum of all payment amounts (we need this much + fee)
    payment_total = sum(p['value_sats'] for p in payments)

    # nSequence and nLockTime from RBF/locktime rules
    nsequence, nlocktime = compute_seq_locktime(fixture)

    # ── Greedy selection ──────────────────────────────────────────────────────
    # Sort largest-first: minimises the number of inputs needed (better privacy + lower fee)
    sorted_utxos = sorted(utxos, key=lambda u: u['value_sats'], reverse=True)

    # Enforce policy.max_inputs: only consider the top N UTXOs
    if max_inputs is not None:
        sorted_utxos = sorted_utxos[:max_inputs]

    # payment outputs as simple dicts for vbytes estimation
    payment_outs = [{'script_type': p.get('script_type', 'unknown')} for p in payments]
    # change output template for vbytes estimation
    change_out_tmpl = {'script_type': change_tmpl.get('script_type', 'unknown')}

    selected = []
    running_total = 0
    result = None  # will be set when we find a valid selection

    for utxo in sorted_utxos:
        selected.append(utxo)
        running_total += utxo['value_sats']

        # ── Case A: include a change output ───────────────────────────────────
        # Estimate tx size with change, compute required fee
        vb_with_change  = estimate_vbytes(selected, payment_outs + [change_out_tmpl])
        fee_with_change = math.ceil(vb_with_change * fee_rate)
        change_amount   = running_total - payment_total - fee_with_change

        if change_amount >= DUST_THRESHOLD:
            # We have enough! change is non-dust, all good.
            result = {
                'selected': selected,
                'fee_sats': fee_with_change,
                'change_sats': change_amount,
                'vbytes': vb_with_change,
                'has_change': True,
            }
            break

        # ── Case B: no change output (leftover becomes fee) ───────────────────
        # Maybe the leftover is just below dust — don't create a dust change output,
        # just absorb the extra into the fee.
        vb_no_change  = estimate_vbytes(selected, payment_outs)
        fee_no_change = math.ceil(vb_no_change * fee_rate)
        leftover      = running_total - payment_total

        if leftover >= fee_no_change:
            # Enough to cover the minimum fee; all leftover goes to miner
            result = {
                'selected': selected,
                'fee_sats': leftover,      # entire leftover = fee
                'change_sats': 0,
                'vbytes': vb_no_change,
                'has_change': False,
            }
            break

        # Not enough yet — continue to the next UTXO

    if result is None:
        raise ValueError("INSUFFICIENT_FUNDS: Not enough UTXOs to cover payments + fee")

    # ── Assemble outputs list ─────────────────────────────────────────────────
    final_outputs = []
    for i, p in enumerate(payments):
        final_outputs.append({
            'n': i,
            'value_sats': p['value_sats'],
            'script_pubkey_hex': p['script_pubkey_hex'],
            'script_type': p.get('script_type', 'unknown'),
            'address': p.get('address', ''),
            'is_change': False,
        })

    change_index = None
    if result['has_change'] and result['change_sats'] >= DUST_THRESHOLD:
        change_index = len(final_outputs)
        final_outputs.append({
            'n': change_index,
            'value_sats': result['change_sats'],
            'script_pubkey_hex': change_tmpl['script_pubkey_hex'],
            'script_type': change_tmpl.get('script_type', 'unknown'),
            'address': change_tmpl.get('address', ''),
            'is_change': True,
        })

    # ── Build PSBT ────────────────────────────────────────────────────────────
    psbt_outputs = [{'value_sats': o['value_sats'],
                     'script_pubkey_hex': o['script_pubkey_hex']} for o in final_outputs]
    psbt_b64 = build_psbt_b64(result['selected'], psbt_outputs, nlocktime, nsequence)

    # ── Compute derived report fields ─────────────────────────────────────────
    fee_sats   = result['fee_sats']
    vbytes     = result['vbytes']
    actual_rate = round(fee_sats / vbytes, 8) if vbytes > 0 else 0

    rbf_signaling  = (nsequence <= 0xFFFFFFFD)
    locktime_type  = classify_locktime(nlocktime)

    # ── Warnings ──────────────────────────────────────────────────────────────
    # Required by spec: HIGH_FEE, DUST_CHANGE, SEND_ALL, RBF_SIGNALING
    warnings = []
    if fee_sats > 1_000_000 or actual_rate > 200:
        warnings.append({'code': 'HIGH_FEE'})
    if not result['has_change']:
        warnings.append({'code': 'SEND_ALL'})
    if result['has_change'] and result['change_sats'] < DUST_THRESHOLD:
        warnings.append({'code': 'DUST_CHANGE'})
    if rbf_signaling:
        warnings.append({'code': 'RBF_SIGNALING'})

    # ── Final report ──────────────────────────────────────────────────────────
    report = {
        'ok': True,
        'network': network,
        'strategy': 'greedy',
        'selected_inputs': [
            {
                'txid': u['txid'],
                'vout': u['vout'],
                'value_sats': u['value_sats'],
                'script_pubkey_hex': u['script_pubkey_hex'],
                'script_type': u.get('script_type', 'unknown'),
                'address': u.get('address', ''),
            }
            for u in result['selected']
        ],
        'outputs': final_outputs,
        'change_index': change_index,
        'fee_sats': fee_sats,
        'fee_rate_sat_vb': actual_rate,
        'vbytes': vbytes,
        'rbf_signaling': rbf_signaling,
        'locktime': nlocktime,
        'locktime_type': locktime_type,
        'psbt_base64': psbt_b64,
        'warnings': warnings,
    }

    return report
