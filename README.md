[![Review Assignment Due Date](https://classroom.github.com/assets/deadline-readme-button-22041afd0340ce965d47ae6ef1cefeee28c7c493a6346c4f15d667ab976d596c.svg)](https://classroom.github.com/a/bK2yja45)
# Week 2 Challenge: Coin Smith

Build a **safe PSBT transaction builder** that:

- selects coins (UTXOs),
- constructs an unsigned Bitcoin transaction,
- exports a **PSBT (BIP-174)**,
- emits a machine-checkable JSON report, and
- serves a small web UI to visualize/justify the result.

This challenge is deliberately **generic**. The public fixtures are not exhaustive. The hidden evaluation includes many more cases across script types, fee/change edge conditions, and malformed inputs. Treat this as a wallet engineering problem: **protocol-first correctness + defensive validation + sensible optimization**.

---

## Problem statement

Given a fixture (UTXO set, one or more payment outputs, a change template, and a fee rate target), produce:

1) a selected input set,
2) a valid PSBT containing an unsigned transaction and required prevout info,
3) a JSON report explaining what you built,
4) the same report via a web API.

There is no single “right” coin selection strategy. Any correct construction is accepted; solutions are **ranked** on a multi-objective cost model (fees, input count, and other wallet-quality signals).

---

## Getting started

Run your first fixture through the CLI:

```bash
./cli.sh fixtures/basic_change_p2wpkh.json
# Output is written to out/basic_change_p2wpkh.json
```

Start the web server:

```bash
./web.sh
```

See [Fixture input format](#fixture-input-format) for a breakdown of what each field in a fixture means.

**Suggested libraries** (use whatever you prefer):

- **JS/TS:** `bitcoinjs-lib`
- **Python:** `python-bitcoinutils` or `bitcoinlib`
- **Rust:** `rust-bitcoin`

---

## Solution requirements

### Must ship (CLI + Web)

1) **CLI builder** (machine-checkable) via:

```
./cli.sh <fixture.json>
```

2) **Web UI** (human-facing) via `./web.sh` with API endpoints below.

### Core expectations

- Parse fixtures defensively (reject malformed inputs with structured errors)
- Select inputs and compute fee + change
- Build a **PSBT** (base64)
- Output a JSON report (schema below)
- Surface safety warnings (you define what “unsafe” means; hidden tests include stress cases)

### Minimum tests

- At least **15 unit tests** (coin selection + fee/change + PSBT structure)

---

## Required repo interface

Your repository must include these scripts:

### 1) `cli.sh <fixture.json>`

- Reads the fixture file (schema below)
- Runs your CLI builder
- Writes the JSON report to `out/<fixture_name>.json` (e.g. `fixtures/basic_change_p2wpkh.json` → `out/basic_change_p2wpkh.json`)
- Creates the `out/` directory if it doesn't exist
- Logs (if any) must go to stderr
- Exit codes:
  - `0` on success
  - `1` on error (invalid fixture, insufficient funds, malformed scripts, etc.)

### 2) `web.sh`

- Starts the web app
- Must print a single line containing the URL (e.g. `http://127.0.0.1:3000`) to stdout
- Must keep running until terminated
- Must honor `PORT` if set (default `3000`)

---

## Fixture input format

Fixture JSON schema:

```json
{
  "network": "mainnet",
  "utxos": [
    {
      "txid": "11...",
      "vout": 0,
      "value_sats": 100000,
      "script_pubkey_hex": "0014...",
      "script_type": "p2wpkh",
      "address": "bc1..."
    }
  ],
  "payments": [
    {
      "address": "bc1...",
      "script_pubkey_hex": "0014...",
      "script_type": "p2wpkh",
      "value_sats": 70000
    }
  ],
  "change": {
    "address": "bc1...",
    "script_pubkey_hex": "0014...",
    "script_type": "p2wpkh"
  },
  "fee_rate_sat_vb": 5,
  "rbf": true,
  "locktime": 850000,
  "current_height": 850000,
  "policy": {
    "max_inputs": 5
  }
}
```

Notes:

- `script_pubkey_hex` is **authoritative** (addresses are for UI only).
- `payments` may contain **multiple outputs** (including repeats).
- `policy.max_inputs` (if provided) must be enforced.
- `rbf`, `locktime`, and `current_height` are optional. See [RBF & Locktime construction](#rbf--locktime-construction) for semantics.
- Fixtures may include **additional fields** (including internal metadata). Ignore what you don't need.

---

## Fee, vbytes, dust, and change

Your transaction must be internally consistent and wallet-safe:

- **Balance:** `sum(inputs) = sum(payment outputs) + sum(change outputs) + fee`
- **Fee target:** fee must be **at least** the target `fee_rate_sat_vb` applied to your transaction’s estimated **vbytes** (use `ceil`). The evaluator uses a deterministic vbytes estimator across common script types.
- **Dust:** do not create dust outputs. For this challenge, treat **546 sats** as the dust threshold.
- **Change:** at most one change output; only create change when it is not dust.
- **Don’t burn sats:** when change exists, your fee should be the **minimum** required to hit the target fee rate (extra leftover should go to change).
- **Be careful:** adding/removing a change output changes transaction size, which changes the required fee. Hidden cases include boundary conditions where naive one-pass change logic fails.

---

## RBF & Locktime construction

Your transaction must set `nSequence` (per input) and `nLockTime` correctly based on fixture fields.

### Fixture fields

| Field | Type | Description |
|-------|------|-------------|
| `rbf` | `bool` | Opt in to BIP-125 Replace-By-Fee. Absent = `false`. |
| `locktime` | `uint32` | Explicit `nLockTime` for the transaction. Absent = not specified. |
| `current_height` | `uint32` | Current chain tip height (for anti-fee-sniping). Absent = unknown. |

### nSequence rules

- `rbf: true` → every input `nSequence = 0xFFFFFFFD`
- `rbf: false` (or absent) with a non-zero `nLockTime` → every input `nSequence = 0xFFFFFFFE` (enables locktime without signaling RBF)
- Otherwise → `nSequence = 0xFFFFFFFF` (final; no RBF, no locktime)

### nLockTime rules

- `locktime` provided → set `nLockTime` to that value
- `locktime` absent, `rbf: true`, AND `current_height` provided → set `nLockTime = current_height` (anti-fee-sniping, per Bitcoin Core behavior)
- Otherwise → `nLockTime = 0`

### Interaction matrix

| rbf | locktime present | current_height | nSequence | nLockTime |
|-----|-----------------|----------------|-----------|-----------|
| false/absent | no | — | 0xFFFFFFFF | 0 |
| false/absent | yes | — | 0xFFFFFFFE | locktime |
| true | no | yes | 0xFFFFFFFD | current_height |
| true | yes | — | 0xFFFFFFFD | locktime |
| true | no | no | 0xFFFFFFFD | 0 |

### Locktime classification

Report the `locktime_type` field based on the final `nLockTime` value:

- `"none"` if `nLockTime == 0`
- `"block_height"` if `0 < nLockTime < 500_000_000`
- `"unix_timestamp"` if `nLockTime >= 500_000_000`

### New report fields

Your JSON report must include these additional fields:

- `rbf_signaling` (`bool`): `true` if any input has `nSequence <= 0xFFFFFFFD`
- `locktime` (`uint32`): the `nLockTime` set on the unsigned transaction
- `locktime_type` (`string`): one of `"none"`, `"block_height"`, `"unix_timestamp"`

---

## CLI output format

Your CLI must write **one JSON object** to the output file with the required fields below (you may add more).

```json
{
  "ok": true,
  "network": "mainnet",
  "strategy": "greedy",
  "selected_inputs": [
    {
      "txid": "...",
      "vout": 0,
      "value_sats": 100000,
      "script_pubkey_hex": "...",
      "script_type": "p2wpkh",
      "address": "bc1..."
    }
  ],
  "outputs": [
    {
      "n": 0,
      "value_sats": 70000,
      "script_pubkey_hex": "...",
      "script_type": "p2wpkh",
      "address": "bc1...",
      "is_change": false
    },
    {
      "n": 1,
      "value_sats": 29300,
      "script_pubkey_hex": "...",
      "script_type": "p2wpkh",
      "address": "bc1...",
      "is_change": true
    }
  ],
  "change_index": 1,
  "fee_sats": 700,
  "fee_rate_sat_vb": 5.0,
  "vbytes": 140,
  "rbf_signaling": true,
  "locktime": 850000,
  "locktime_type": "block_height",
  "psbt_base64": "cHNidP8BAFICAAAA...",
  "warnings": [
    { "code": "SEND_ALL" },
    { "code": "RBF_SIGNALING" }
  ]
}
```

Field requirements:

- `selected_inputs` must be a subset of fixture `utxos`.
- `outputs` must include **all payments** and **at most one change output**.
- `change_index` must be `null` if there is no change output.
- `psbt_base64` must decode to a valid PSBT containing:
  - a global unsigned transaction
  - sufficient prevout information for each input (`witness_utxo` and/or `non_witness_utxo`)
- `fee_rate_sat_vb` must equal `fee_sats / vbytes` (±0.01 allowed).

### Error output

On error, write to the same output file:

```json
{ "ok": false, "error": { "code": "INVALID_FIXTURE", "message": "..." } }
```

Both `error.code` and `error.message` must be **non-empty strings**.

---

## Required warnings (codes)

Emit warning codes when:

- `HIGH_FEE`: `fee_sats > 1_000_000` **OR** `fee_rate_sat_vb > 200`
- `DUST_CHANGE`: a change output exists with `value_sats < 546`
- `SEND_ALL`: no change output was created (leftover consumed as fee)
- `RBF_SIGNALING`: `rbf_signaling` is `true` (transaction opts into Replace-By-Fee)

(You may add more warnings.)

---

## Web UI requirements (candidate-facing)

Your web app must:

- Let the user load a fixture JSON
- Visualize selected inputs and outputs
- Clearly identify the change output (if present)
- Show fee, fee rate, and warnings
- Display RBF signaling status and locktime info when present

Minimum UI content visible:

- Total inputs / outputs
- Fee + fee rate
- Selected input list
- Output list with script types + "change" badge
- RBF signaling indicator
- Locktime value and type (when non-zero)

---

## Sample tests

Public fixtures are in `fixtures/`.

Examples:

```bash
./cli.sh fixtures/basic_change_p2wpkh.json
jq '.fee_sats,.change_index,.outputs' out/basic_change_p2wpkh.json
```

```bash
./cli.sh fixtures/send_all_dust_change.json
jq '.warnings' out/send_all_dust_change.json
```

---

## Hidden fixture categories (what we test)

The hidden evaluation covers at least these categories. You don't know the exact fixtures, but your implementation must handle all of them correctly:

- `rbf: true` — basic RBF signaling
- `rbf: false` — explicit opt-out (no RBF, no locktime → `nSequence = 0xFFFFFFFF`)
- `rbf: true` with multiple inputs — all inputs must signal (`nSequence = 0xFFFFFFFD`)
- `locktime` set to a block height (e.g. `850000`)
- `locktime` set to a unix timestamp (e.g. `1700000000`)
- Anti-fee-sniping: `rbf: true` + `current_height` present, no explicit `locktime` → `nLockTime = current_height`
- `locktime` present but `rbf: false` → `nSequence = 0xFFFFFFFE` (locktime enabled without RBF)
- Locktime boundary: `499999999` (block height) vs `500000000` (unix timestamp)
- `rbf: true` + send-all (no change; RBF fields still apply)
- Neither `rbf` nor `locktime` (backward compatibility — defaults only)

---

## Acceptance criteria (definitive)

- `cli.sh` succeeds on all provided fixtures
- PSBT is structurally valid and matches reported inputs/outputs
- Amounts balance correctly (inputs = outputs + fee)
- Fee/change/dust handling is wallet-safe and consistent
- RBF/locktime: `nSequence` and `nLockTime` in the PSBT unsigned transaction match the interaction matrix for the given fixture fields
- Report includes correct `rbf_signaling`, `locktime`, and `locktime_type` values
- Tests pass (`grade.sh`)
- Week 1 acceptance tests still pass (regressions are penalized)
- Demo video link is included in `demo.md` at the repository root (the file should contain only the link):
  - **Where to upload:** YouTube, Loom, or Google Drive. The link must be viewable by evaluators without requesting access (public or unlisted is fine; no “request access” links).
  - **What to record:** a screen recording of your **web UI** walkthrough (no code walkthrough; don’t spend time scrolling through source files).
  - **What to demonstrate:** load **one** fixture in your UI, build a PSBT, and walk through the chosen inputs and created outputs using the UI.
  - **How to explain:** speak as if to a non-technical person who has never seen Bitcoin before; use simple language and define terms as you introduce them.
  - **Topics your walkthrough must cover (using the UI):**
    - what a wallet is tracking (UTXOs) and what it means to "select coins"
    - which inputs were selected and why (in simple terms)
    - payment outputs vs the change output (what "change" is and why it exists)
    - dust rules (why tiny outputs are unsafe/useless)
    - fee rate → vbytes → fee (why transaction size affects fee; why change can affect fee)
    - send-all vs creating change (what it means when leftover becomes fee)
    - what a PSBT is at a high level (an unsigned transaction packaged with metadata so it can be safely signed)
    - RBF: what Replace-By-Fee means (why a sender might want to bump a fee) and how `nSequence` signals it
    - timelocks: what `nLockTime` does (why a transaction might be locked until a certain block or time)
    - any warnings your UI shows (high fee, dust change, send-all, RBF signaling, etc.)
  - **Hard limit:** the video must be strictly **less than 2 minutes** long.

---

## Stretch goals (optional, but good for learning)

- Multiple coin selection strategies + compare scores
- Sign PSBT with test keys and finalize tx hex
- Export watch-only descriptors
- “Privacy meter” for input reuse + output linkage risk

---

## Key learnings

- UTXO management and coin selection tradeoffs
- PSBT workflow and safe output construction
- Wallet UX: explaining fees, change, and risk
- RBF signaling via `nSequence` and BIP-125 opt-in
- Locktime semantics: block height vs unix timestamp, anti-fee-sniping

---

## Evaluation criteria

Evaluation happens in two phases:

### Phase 1: Automated evaluation (before deadline)

- **CLI correctness:** your `cli.sh` will be run against all public fixtures.
- **Web health check:** `web.sh` must start successfully and respond to `GET /api/health` with `200 { "ok": true }`.
- **Error handling:** invalid inputs must produce structured error JSON (`{ "ok": false, "error": { "code": "...", "message": "..." } }`) and exit code `1`.

### Phase 2: Manual evaluation (after deadline)

- **Hidden fixtures:** your CLI will be tested against a broad set of hidden fixtures covering the scenarios listed above.
- **Web UI quality:** clarity of explanations, visual design, diagrams, and how well the UI teaches non-technical users about Bitcoin transactions.
- **Demo video:** evaluated for coverage of required topics, clarity of explanation, and adherence to the 2-minute time limit.
- **Code quality:** readability, structure, and appropriate use of abstractions.

---

## Do not modify

The following files and directories are part of the evaluation infrastructure and **must not be edited**:

- `grade.sh` — automated grading script
- `fixtures/` — public test fixtures
- `.github/` — CI/CD workflow configuration

Modifying any of these may cause your submission to fail manual evaluation.

---

## Plagiarism policy

- All submitted code must be your own original work. You may use AI coding assistants (e.g. GitHub Copilot, ChatGPT, Claude) as tools, but you must understand and be able to explain every part of your submission.
- Copying code from other participants' submissions (current or past cohorts) is strictly prohibited.
- Using open-source libraries and referencing public documentation (BIPs, Bitcoin wiki, Stack Exchange, etc.) is encouraged — that is research, not plagiarism.
- Submissions will be checked for similarity against other participants. If two or more submissions share substantially identical logic or structure beyond what would arise from following the spec, all involved submissions may be disqualified.
- If you are unsure whether something counts as plagiarism, ask before submitting.


