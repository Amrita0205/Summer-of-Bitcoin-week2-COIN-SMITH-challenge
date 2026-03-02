"""
bitcoin_tx.py — Bitcoin transaction primitives, pure Python (no external deps).

What this file does, step by step:
  1. varint()           — encode integers the Bitcoin way (variable-length)
  2. detect_script_type() — figure out script type from raw scriptPubKey bytes
  3. is_segwit()        — is this a segwit script type?
  4. estimate_vbytes()  — how big is this tx in virtual bytes (for fee calc)
  5. serialize_unsigned_tx() — raw bytes of the unsigned transaction
  6. build_psbt_b64()   — wrap the unsigned tx in a BIP-174 PSBT, return base64
"""

import struct
import base64
import math


# ─────────────────────────────────────────────────────────────────────────────
# 1. Variable-length integer encoding (varint)
# Bitcoin uses this to encode counts and lengths compactly.
# ─────────────────────────────────────────────────────────────────────────────

def varint(n: int) -> bytes:
    """
    Encode n as a Bitcoin variable-length integer.
    - 0..0xfc        → 1 byte
    - 0xfd..0xffff   → 0xfd + 2 bytes LE
    - 0x10000..      → 0xfe + 4 bytes LE
    """
    if n < 0xfd:
        return bytes([n])
    elif n <= 0xffff:
        return b'\xfd' + struct.pack('<H', n)
    elif n <= 0xffffffff:
        return b'\xfe' + struct.pack('<I', n)
    else:
        return b'\xff' + struct.pack('<Q', n)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Script type detection
# The spec says script_pubkey_hex is authoritative — we detect from raw bytes.
# ─────────────────────────────────────────────────────────────────────────────

def detect_script_type(script_hex: str) -> str:
    """
    Detect Bitcoin script type from scriptPubKey hex.

    Pattern matching on standard script templates:
      P2PKH  : 76 a9 14 <20 bytes> 88 ac   (25 bytes)
      P2SH   : a9 14 <20 bytes> 87          (23 bytes)
      P2WPKH : 00 14 <20 bytes>             (22 bytes)
      P2WSH  : 00 20 <32 bytes>             (34 bytes)
      P2TR   : 51 20 <32 bytes>             (34 bytes, OP_1 = 0x51)
    """
    try:
        s = bytes.fromhex(script_hex)
    except (ValueError, AttributeError):
        return "unknown"

    n = len(s)

    # P2PKH: OP_DUP OP_HASH160 OP_DATA(20) ... OP_EQUALVERIFY OP_CHECKSIG
    if n == 25 and s[0] == 0x76 and s[1] == 0xa9 and s[2] == 0x14 \
            and s[23] == 0x88 and s[24] == 0xac:
        return "p2pkh"

    # P2SH: OP_HASH160 OP_DATA(20) ... OP_EQUAL
    if n == 23 and s[0] == 0xa9 and s[1] == 0x14 and s[22] == 0x87:
        return "p2sh"

    # P2WPKH: OP_0 OP_DATA(20) ...
    if n == 22 and s[0] == 0x00 and s[1] == 0x14:
        return "p2wpkh"

    # P2WSH: OP_0 OP_DATA(32) ...
    if n == 34 and s[0] == 0x00 and s[1] == 0x20:
        return "p2wsh"

    # P2TR: OP_1 OP_DATA(32) ...  (OP_1 = 0x51)
    if n == 34 and s[0] == 0x51 and s[1] == 0x20:
        return "p2tr"

    return "unknown"


def is_segwit(stype: str) -> bool:
    """Return True for native segwit and wrapped segwit script types."""
    return stype in ("p2wpkh", "p2wsh", "p2tr", "p2sh-p2wpkh", "p2sh_p2wpkh")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Transaction size / vbytes estimation
#
# Bitcoin uses "weight units" (wu) to measure tx size for fee purposes:
#   - Every non-witness byte = 4 wu
#   - Every witness byte     = 1 wu
#   - vbytes = ceil(total_weight / 4)
#
# This lets segwit txs pay lower fees because their witness data is "discounted".
#
# Per-input sizes (how many bytes each input type contributes):
#
#   Non-witness part of any input:
#     txid(32) + vout(4) + scriptSig_len(1) + sequence(4) = 41 bytes → ×4 = 164 wu
#
#   Then we add the scriptSig and/or witness for each type:
#
#   p2pkh:  scriptSig = <sig(73)><pubkey(33)> = 108 bytes → ×4 = 432 wu, no witness
#   p2sh:   scriptSig varies; use 2-of-3 multisig estimate = 254 bytes → ×4 = 1016 wu
#   p2wpkh: scriptSig = empty (already in 41 bytes above)
#           witness = item_count(1) + sig_len(1)+sig(72) + pub_len(1)+pub(33) = 108 wu ×1
#   p2wsh:  scriptSig = empty
#           witness ~ 2-of-2 multisig = item_count(1)+...+script = 173 wu ×1
#   p2tr:   scriptSig = empty (key path spend)
#           witness = item_count(1) + sig_len(1) + schnorr_sig(64) = 66 wu ×1
#
# Per-output sizes (all non-witness → ×4):
#   value(8) + script_len(1) + script = (9 + script_size) bytes → ×4
#   p2pkh  : 9+25 = 34 bytes → 136 wu
#   p2sh   : 9+23 = 32 bytes → 128 wu
#   p2wpkh : 9+22 = 31 bytes → 124 wu
#   p2wsh  : 9+34 = 43 bytes → 172 wu
#   p2tr   : 9+34 = 43 bytes → 172 wu
#
# Tx overhead (non-witness):
#   version(4) + vin_count_varint(1) + vout_count_varint(1) + locktime(4) = 10 bytes → 40 wu
#   If any segwit input: +2 wu for segwit marker (0x00 0x01)
# ─────────────────────────────────────────────────────────────────────────────

# (input_non_witness_extra_wu, witness_wu)
# The 164 wu base (txid+vout+scriptsig_len+seq) is always added separately.
_INPUT_WU = {
    "p2pkh":        (108 * 4, 0),    # scriptSig=108 bytes, no witness
    "p2sh":         (254 * 4, 0),    # scriptSig~254 bytes (2-of-3), no witness
    "p2wpkh":       (0,       108),  # empty scriptSig, witness=108 wu
    "p2wsh":        (0,       173),  # empty scriptSig, witness~173 wu
    "p2sh-p2wpkh":  (23 * 4,  108), # scriptSig=push(22-byte script), witness=108
    "p2sh_p2wpkh":  (23 * 4,  108),
    "p2tr":         (0,       66),   # key path: witness=66 wu
    "unknown":      (108 * 4, 0),   # fallback: treat like p2pkh
}

# Output weight in wu (all non-witness)
_OUTPUT_WU = {
    "p2pkh":       34 * 4,
    "p2sh":        32 * 4,
    "p2wpkh":      31 * 4,
    "p2wsh":       43 * 4,
    "p2tr":        43 * 4,
    "p2sh-p2wpkh": 32 * 4,
    "p2sh_p2wpkh": 32 * 4,
    "unknown":     34 * 4,
}

_INPUT_BASE_WU = 41 * 4  # txid+vout+scriptsig_len+sequence, all non-witness
_TX_OVERHEAD_WU = 10 * 4  # version + vin_count + vout_count + locktime
_SEGWIT_OVERHEAD_WU = 2   # marker(1) + flag(1), counted as witness bytes


def estimate_vbytes(inputs: list, outputs: list) -> int:
    """
    Estimate transaction size in virtual bytes.
    inputs:  list of dicts with 'script_type'
    outputs: list of dicts with 'script_type'
    Returns: int vbytes (ceiling)
    """
    has_sw = any(is_segwit(i.get("script_type", "unknown")) for i in inputs)

    total_wu = _TX_OVERHEAD_WU
    if has_sw:
        total_wu += _SEGWIT_OVERHEAD_WU

    for inp in inputs:
        stype = inp.get("script_type", "unknown")
        nw_extra, wit = _INPUT_WU.get(stype, _INPUT_WU["unknown"])
        total_wu += _INPUT_BASE_WU + nw_extra + wit

    for out in outputs:
        stype = out.get("script_type", "unknown")
        total_wu += _OUTPUT_WU.get(stype, _OUTPUT_WU["unknown"])

    return math.ceil(total_wu / 4)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Serialize unsigned transaction
#
# Bitcoin transaction format (non-segwit serialization for PSBT):
#   version (4 bytes LE)
#   vin count (varint)
#   for each input:
#     txid (32 bytes, little-endian — txid displayed as big-endian hex must be reversed)
#     vout (4 bytes LE)
#     scriptSig length = 0x00 (empty for unsigned tx)
#     sequence (4 bytes LE)
#   vout count (varint)
#   for each output:
#     value (8 bytes LE, satoshis)
#     scriptPubKey length (varint)
#     scriptPubKey bytes
#   locktime (4 bytes LE)
#
# NOTE: For PSBT the unsigned tx MUST NOT have witness data or scriptSigs.
# ─────────────────────────────────────────────────────────────────────────────

def serialize_unsigned_tx(inputs: list, outputs: list,
                          locktime: int, nsequence: int) -> bytes:
    """
    Build raw unsigned transaction bytes (for PSBT global map).

    inputs:   list of dicts with keys: txid (hex str), vout (int)
    outputs:  list of dicts with keys: value_sats (int), script_pubkey_hex (str)
    locktime: nLockTime value (uint32)
    nsequence: same sequence for all inputs

    TXID reversal: Bitcoin stores txid bytes in little-endian order internally,
    but we display them as big-endian hex. So we reverse when encoding.
    """
    buf = b''

    # version = 2 (standard for modern wallets)
    buf += struct.pack('<I', 2)

    # inputs
    buf += varint(len(inputs))
    for inp in inputs:
        # txid: reverse from display format (big-endian) to wire format (little-endian)
        buf += bytes.fromhex(inp['txid'])[::-1]
        buf += struct.pack('<I', inp['vout'])
        buf += b'\x00'  # empty scriptSig (unsigned)
        buf += struct.pack('<I', nsequence)

    # outputs
    buf += varint(len(outputs))
    for out in outputs:
        buf += struct.pack('<q', out['value_sats'])
        spk = bytes.fromhex(out['script_pubkey_hex'])
        buf += varint(len(spk))
        buf += spk

    buf += struct.pack('<I', locktime)
    return buf


# ─────────────────────────────────────────────────────────────────────────────
# 5. Build PSBT (BIP-174)
#
# PSBT layout:
#   Magic: b"psbt" + 0xff
#   Global map:
#     key=0x00 : unsigned transaction bytes
#     0x00 (separator)
#   Per-input maps (one per input in the unsigned tx):
#     For segwit inputs (key=0x01): witness_utxo = value(8 LE) + scriptPubKey
#     For non-segwit (key=0x00):   non_witness_utxo = full previous tx
#     0x00 (separator)
#   Per-output maps (one per output, all empty for unsigned):
#     0x00 (separator)
#
# Each key-value entry: varint(key_len) + key_bytes + varint(val_len) + val_bytes
# ─────────────────────────────────────────────────────────────────────────────

PSBT_MAGIC = b'psbt\xff'


def _kv(key: bytes, val: bytes) -> bytes:
    """Encode one PSBT key-value pair."""
    return varint(len(key)) + key + varint(len(val)) + val


def _make_prev_tx(utxo: dict) -> bytes:
    """
    Build a minimal previous transaction for non-segwit inputs.
    The evaluator needs the full previous tx to verify non-segwit inputs.
    We create a minimal tx with the correct output at the right vout index.
    """
    vout_idx = utxo['vout']
    spk = bytes.fromhex(utxo['script_pubkey_hex'])

    # version = 1
    buf = struct.pack('<I', 1)

    # One dummy coinbase-style input (so the tx is valid structure-wise)
    buf += varint(1)
    buf += b'\x00' * 32           # prev txid = zeros
    buf += struct.pack('<I', 0xffffffff)  # prev vout
    buf += varint(4) + b'\x00\x00\x00\x00'  # scriptSig (4-byte coinbase data)
    buf += struct.pack('<I', 0xffffffff)  # sequence

    # Outputs: pad with OP_RETURN dummies up to our vout index, then the real one
    num_out = vout_idx + 1
    buf += varint(num_out)
    for i in range(num_out):
        if i == vout_idx:
            buf += struct.pack('<q', utxo['value_sats'])
            buf += varint(len(spk)) + spk
        else:
            # Unspendable dummy output (OP_RETURN)
            buf += struct.pack('<q', 0)
            buf += varint(1) + b'\x6a'

    buf += struct.pack('<I', 0)  # locktime = 0
    return buf


def build_psbt_b64(selected_inputs: list, outputs: list,
                   locktime: int, nsequence: int) -> str:
    """
    Build a BIP-174 PSBT and return it as a base64 string.

    selected_inputs: list of UTXO dicts (with txid, vout, value_sats,
                     script_pubkey_hex, script_type)
    outputs:         list of output dicts (value_sats, script_pubkey_hex, script_type)
    locktime:        nLockTime for the transaction
    nsequence:       nSequence value (same for all inputs per spec)
    """
    # Serialize the unsigned transaction (goes in global map)
    unsigned_tx = serialize_unsigned_tx(selected_inputs, outputs, locktime, nsequence)

    buf = PSBT_MAGIC

    # ── Global map ────────────────────────────────────────────────────────────
    # Key 0x00 = PSBT_GLOBAL_UNSIGNED_TX
    buf += _kv(b'\x00', unsigned_tx)
    buf += b'\x00'  # end of global map

    # ── Per-input maps ────────────────────────────────────────────────────────
    for utxo in selected_inputs:
        stype = utxo.get('script_type', 'unknown')
        spk = bytes.fromhex(utxo['script_pubkey_hex'])

        if is_segwit(stype):
            # PSBT_IN_WITNESS_UTXO (key=0x01):
            # The previous output this input is spending: value (8 bytes LE) + scriptPubKey
            witness_utxo = struct.pack('<q', utxo['value_sats']) + varint(len(spk)) + spk
            buf += _kv(b'\x01', witness_utxo)
        else:
            # PSBT_IN_NON_WITNESS_UTXO (key=0x00):
            # For legacy inputs, we include the full previous tx so the signer
            # can verify the amount being spent.
            prev_tx = _make_prev_tx(utxo)
            buf += _kv(b'\x00', prev_tx)

        buf += b'\x00'  # end of this input's map

    # ── Per-output maps (empty — nothing to add for unsigned tx) ──────────────
    for _ in outputs:
        buf += b'\x00'

    return base64.b64encode(buf).decode('ascii')
