#!/usr/bin/env python3
"""
web.py — Web server for Coin Smith PSBT builder.

Endpoints:
  GET  /           → the web UI
  GET  /api/health → {"ok": true}
  POST /api/build  → accepts fixture JSON body, returns report JSON
"""

import os
import sys
import json
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, request, jsonify, render_template_string
from src.builder import validate_fixture, select_coins

app = Flask(__name__)

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Coin Smith</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Barlow:wght@300;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg:      #080808;
  --surface: #0e0e0e;
  --border:  #1e1e1e;
  --border2: #2a2a2a;
  --amber:   #f5a623;
  --amber2:  #c27d0e;
  --dim:     #444;
  --muted:   #666;
  --text:    #d4d4d4;
  --bright:  #f0f0f0;
  --green:   #4caf7d;
  --red:     #e05252;
  --blue:    #5b9bd5;
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html, body {
  height: 100%;
  background: var(--bg);
  color: var(--text);
  font-family: 'Share Tech Mono', monospace;
  font-size: 12px;
  line-height: 1.5;
  overflow: hidden;
}

/* ── Scanline overlay ── */
body::before {
  content: '';
  position: fixed; inset: 0;
  background: repeating-linear-gradient(
    0deg,
    transparent,
    transparent 2px,
    rgba(0,0,0,0.06) 2px,
    rgba(0,0,0,0.06) 4px
  );
  pointer-events: none;
  z-index: 9999;
}

/* ── Header ── */
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  height: 48px;
  border-bottom: 1px solid var(--border2);
  background: var(--surface);
  position: relative;
  overflow: hidden;
}

/* animated amber "trace" line at bottom of header */
header::after {
  content: '';
  position: absolute;
  bottom: 0; left: -100%;
  width: 60%;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--amber), transparent);
  animation: trace 3.5s ease-in-out infinite;
}
@keyframes trace {
  0%   { left: -60%; }
  100% { left: 160%; }
}

.logo {
  font-family: 'Barlow', sans-serif;
  font-weight: 700;
  font-size: 15px;
  letter-spacing: 3px;
  text-transform: uppercase;
  color: var(--bright);
}
.logo span { color: var(--amber); }

.header-meta {
  font-size: 10px;
  color: var(--muted);
  letter-spacing: 1px;
}

.status-dot {
  display: inline-block;
  width: 6px; height: 6px;
  border-radius: 50%;
  background: var(--green);
  margin-right: 6px;
  box-shadow: 0 0 6px var(--green);
  animation: pulse 2s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.4; }
}

/* ── Layout ── */
.workspace {
  display: grid;
  grid-template-columns: 420px 1fr;
  height: calc(100vh - 48px);
}

/* ── Left panel ── */
.left {
  border-right: 1px solid var(--border2);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.panel-label {
  padding: 10px 16px 8px;
  font-size: 9px;
  letter-spacing: 2px;
  text-transform: uppercase;
  color: var(--muted);
  border-bottom: 1px solid var(--border);
  background: var(--surface);
}

.panel-label .hl { color: var(--amber); }

textarea {
  flex: 1;
  resize: none;
  background: var(--bg);
  color: #a8c488;
  border: none;
  outline: none;
  padding: 14px 16px;
  font-family: 'Share Tech Mono', monospace;
  font-size: 11px;
  line-height: 1.8;
  caret-color: var(--amber);
}
textarea::placeholder { color: #333; }
textarea:focus { background: #090909; }

.build-zone {
  padding: 12px 16px;
  border-top: 1px solid var(--border2);
  background: var(--surface);
}

.build-btn {
  width: 100%;
  padding: 10px;
  background: transparent;
  border: 1px solid var(--amber2);
  color: var(--amber);
  font-family: 'Share Tech Mono', monospace;
  font-size: 11px;
  letter-spacing: 2px;
  text-transform: uppercase;
  cursor: pointer;
  position: relative;
  overflow: hidden;
  transition: border-color 0.2s, color 0.2s;
}
.build-btn::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--amber);
  transform: translateX(-101%);
  transition: transform 0.2s ease;
}
.build-btn:hover::before  { transform: translateX(0); }
.build-btn:hover          { color: #000; border-color: var(--amber); }
.build-btn span           { position: relative; z-index: 1; }
.build-btn:disabled       { opacity: 0.3; cursor: not-allowed; }
.build-btn:disabled::before { display: none; }

.clear-btn {
  width: 100%;
  padding: 7px;
  margin-top: 6px;
  background: transparent;
  border: 1px solid rgba(224, 82, 82, 0.4);
  color: rgba(224, 82, 82, 0.7);
  font-family: 'Share Tech Mono', monospace;
  font-size: 10px;
  letter-spacing: 2px;
  text-transform: uppercase;
  cursor: pointer;
  transition: border-color 0.2s, color 0.2s, background 0.2s;
}
.clear-btn:hover {
  border-color: #e05252;
  color: #e05252;
  background: rgba(224, 82, 82, 0.05);
}

#status-line {
  margin-top: 8px;
  font-size: 10px;
  min-height: 14px;
  letter-spacing: 0.5px;
}

/* glossary strip */
.glossary {
  border-top: 1px solid var(--border);
  padding: 12px 16px;
  background: var(--surface);
  overflow-y: auto;
  max-height: 200px;
}
.glos-title {
  font-size: 9px;
  letter-spacing: 2px;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 8px;
}
.glos-row {
  display: flex;
  gap: 8px;
  margin-bottom: 5px;
  font-size: 10px;
  line-height: 1.5;
}
.glos-key {
  color: var(--amber);
  min-width: 90px;
  flex-shrink: 0;
}
.glos-val { color: var(--muted); }

/* ── Right panel ── */
.right {
  overflow-y: auto;
  padding: 20px 24px;
  background: var(--bg);
}

.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 12px;
  color: var(--border2);
}
.empty-glyph {
  font-size: 48px;
  line-height: 1;
  opacity: 0.4;
}
.empty-text {
  font-size: 11px;
  letter-spacing: 1px;
  color: var(--dim);
}

/* ── Result sections ── */
.section { margin-bottom: 20px; }

.sec-head {
  font-size: 9px;
  letter-spacing: 2px;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 10px;
  padding-bottom: 5px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 8px;
}
.sec-head .count {
  color: var(--amber);
  font-size: 10px;
}

/* Flow bar */
.flow-bar {
  display: flex;
  align-items: stretch;
  gap: 0;
  margin-bottom: 20px;
  border: 1px solid var(--border2);
  overflow: hidden;
}
.flow-cell {
  flex: 1;
  padding: 10px 14px;
  border-right: 1px solid var(--border2);
  background: var(--surface);
}
.flow-cell:last-child { border-right: none; }
.flow-cell .fc-val {
  font-family: 'Barlow', sans-serif;
  font-weight: 600;
  font-size: 18px;
  color: var(--amber);
  line-height: 1;
}
.flow-cell.fee .fc-val { color: #d29922; font-size: 14px; }
.flow-cell.chg .fc-val { color: var(--blue); }
.flow-cell .fc-lbl {
  font-size: 9px;
  color: var(--muted);
  letter-spacing: 1px;
  text-transform: uppercase;
  margin-top: 3px;
}

/* Stats grid */
.stats-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1px;
  background: var(--border);
  border: 1px solid var(--border2);
  margin-bottom: 20px;
}
.stat-cell {
  background: var(--surface);
  padding: 8px 12px;
}
.stat-cell .sk { font-size: 9px; color: var(--muted); letter-spacing: 1px; text-transform: uppercase; }
.stat-cell .sv { font-size: 12px; color: var(--bright); margin-top: 2px; }
.stat-cell .sv.amber { color: var(--amber); }
.stat-cell .sv.green { color: var(--green); }
.stat-cell .sv.blue  { color: var(--blue); }
.stat-cell .sv.dim   { color: var(--dim); }

/* UTXO / output rows */
.tx-row {
  border: 1px solid var(--border);
  background: var(--surface);
  padding: 9px 12px;
  margin-bottom: 4px;
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 4px;
  align-items: start;
  transition: border-color 0.15s;
}
.tx-row:hover { border-color: var(--border2); }

.tx-row .txid {
  font-size: 9px;
  color: var(--blue);
  word-break: break-all;
  line-height: 1.4;
}
.tx-row .txid .vout { color: var(--amber); }
.tx-row .addr { font-size: 9px; color: var(--muted); margin-top: 2px; }
.tx-row .amount { font-size: 12px; color: var(--bright); font-family: 'Barlow', sans-serif; font-weight: 600; text-align: right; }
.tx-row .amount .unit { font-size: 9px; color: var(--muted); font-family: 'Share Tech Mono', monospace; font-weight: 300; }

/* badges */
.tag {
  display: inline-block;
  padding: 1px 5px;
  font-size: 8px;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  border: 1px solid;
  margin-right: 3px;
}
.tag-type    { color: var(--dim);   border-color: var(--border2); }
.tag-payment { color: var(--green); border-color: #2a4a36; }
.tag-change  { color: var(--blue);  border-color: #2a3d50; }

/* RBF pill */
.rbf-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px;
  border: 1px solid;
  font-size: 9px;
  letter-spacing: 1px;
  text-transform: uppercase;
}
.rbf-on  { border-color: #5a4010; color: var(--amber); background: #1a1200; }
.rbf-off { border-color: var(--border2); color: var(--dim); }

.rbf-dot {
  width: 5px; height: 5px; border-radius: 50%;
  background: currentColor;
}
.rbf-on .rbf-dot { animation: pulse 1.5s ease-in-out infinite; }

/* locktime chip */
.lt-chip {
  display: inline-block;
  padding: 3px 8px;
  font-size: 9px;
  letter-spacing: 1px;
  border: 1px solid var(--border2);
  color: var(--blue);
  background: #0a0f18;
}
.lt-none { color: var(--dim); background: transparent; }

/* Warnings */
.warn-row {
  padding: 7px 10px;
  margin-bottom: 4px;
  font-size: 10px;
  border-left: 2px solid;
  letter-spacing: 0.3px;
}
.w-HIGH_FEE     { border-color: var(--red);   color: #c06060; background: #150808; }
.w-SEND_ALL     { border-color: #d29922;       color: #a87820; background: #120e00; }
.w-RBF_SIGNALING{ border-color: var(--blue);  color: #4a7aaa; background: #080e18; }
.w-DUST_CHANGE  { border-color: var(--red);   color: #c06060; background: #150808; }
.w-other        { border-color: var(--border2);color: var(--muted); }

/* PSBT block */
.psbt-wrap {
  background: var(--surface);
  border: 1px solid var(--border2);
  overflow: hidden;
}
.psbt-text {
  font-size: 9px;
  color: #3a6e3a;
  word-break: break-all;
  line-height: 1.6;
  max-height: 130px;
  overflow-y: auto;
  padding: 10px 12px;
  scrollbar-width: thin;
  scrollbar-color: #2a2a2a #0e0e0e;
}
.psbt-text::-webkit-scrollbar { width: 4px; }
.psbt-text::-webkit-scrollbar-track { background: #0e0e0e; }
.psbt-text::-webkit-scrollbar-thumb { background: #2a2a2a; border-radius: 2px; }
.psbt-text::-webkit-scrollbar-thumb:hover { background: var(--amber2); }
.psbt-copy {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  width: 100%;
  padding: 9px;
  background: transparent;
  border: none;
  border-top: 1px solid var(--border2);
  color: var(--muted);
  font-family: 'Share Tech Mono', monospace;
  font-size: 10px;
  letter-spacing: 2px;
  cursor: pointer;
  text-transform: uppercase;
  transition: background 0.2s, color 0.2s;
}
.psbt-copy:hover { background: rgba(245,166,35,0.06); color: var(--amber); border-top-color: var(--amber2); }
.psbt-copy .copy-icon { font-size: 11px; opacity: 0.7; }

.err-block {
  border: 1px solid #3a1010;
  background: #100505;
  padding: 14px 16px;
  color: var(--red);
  font-size: 11px;
  line-height: 1.7;
}
.err-block .ec { color: #ff6b6b; font-family: 'Barlow', sans-serif; font-weight: 600; font-size: 13px; }
</style>
</head>
<body>

<header>
  <div class="logo">COIN<span>·</span>SMITH</div>
  <div class="header-meta">
    <span class="status-dot"></span>PSBT BUILDER &nbsp;/&nbsp; BIP-174 &nbsp;/&nbsp; MAINNET
  </div>
</header>

<div class="workspace">

  <!-- LEFT: input -->
  <div class="left">
    <div class="panel-label">// <span class="hl">FIXTURE</span> INPUT</div>
    <textarea id="fixture-input" spellcheck="false"
      placeholder='{
  "network": "mainnet",
  "utxos": [...],
  "payments": [...],
  "change": {...},
  "fee_rate_sat_vb": 5
}'></textarea>

    <div class="build-zone">
      <button class="build-btn" id="build-btn" onclick="buildPSBT()">
        <span>&#9656; BUILD PSBT</span>
      </button>
      <button class="clear-btn" onclick="clearAll()">&#x2715; CLEAR</button>
      <div id="status-line"></div>
    </div>

    <div class="glossary">
      <div class="glos-title">// REFERENCE</div>
      <div class="glos-row"><span class="glos-key">UTXO</span><span class="glos-val">Unspent coin your wallet controls — like a physical bill</span></div>
      <div class="glos-row"><span class="glos-key">COIN SELECT</span><span class="glos-val">Picking which UTXOs to spend (greedy: largest first)</span></div>
      <div class="glos-row"><span class="glos-key">CHANGE</span><span class="glos-val">Leftover sent back to yourself; omitted if below dust</span></div>
      <div class="glos-row"><span class="glos-key">DUST (546)</span><span class="glos-val">Output too tiny to spend — never created</span></div>
      <div class="glos-row"><span class="glos-key">FEE</span><span class="glos-val">vbytes × fee_rate — larger tx = more bytes = more fee</span></div>
      <div class="glos-row"><span class="glos-key">PSBT</span><span class="glos-val">Unsigned tx + metadata, ready for hardware wallet signing</span></div>
      <div class="glos-row"><span class="glos-key">RBF</span><span class="glos-val">nSeq ≤ 0xFFFFFFFD — allows fee-bumping a stuck tx</span></div>
      <div class="glos-row"><span class="glos-key">LOCKTIME</span><span class="glos-val">Tx unmineable until block height or unix timestamp</span></div>
      <div class="glos-row"><span class="glos-key">SEND ALL</span><span class="glos-val">No change created — all leftover becomes miner fee</span></div>
    </div>
  </div>

  <!-- RIGHT: results -->
  <div class="right" id="results-pane">
    <div class="empty">
      <div class="empty-glyph">₿</div>
      <div class="empty-text">AWAITING FIXTURE INPUT</div>
    </div>
  </div>

</div>

<script>
async function buildPSBT() {
  const raw = document.getElementById('fixture-input').value.trim();
  if (!raw) { setStatus('! paste fixture JSON first', '#e05252'); return; }

  let fixture;
  try { fixture = JSON.parse(raw); }
  catch(e) { setStatus('! json parse error: ' + e.message, '#e05252'); return; }

  setStatus('> building...', '#5b9bd5');
  document.getElementById('build-btn').disabled = true;

  try {
    const r = await fetch('/api/build', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(fixture),
    });
    const data = await r.json();
    if (data.ok) {
      setStatus('> psbt built successfully', '#4caf7d');
      renderResult(data);
    } else {
      setStatus('! ' + data.error.code, '#e05252');
      showError(data.error);
    }
  } catch(e) {
    setStatus('! request failed: ' + e.message, '#e05252');
  } finally {
    document.getElementById('build-btn').disabled = false;
  }
}

function setStatus(msg, color) {
  const el = document.getElementById('status-line');
  el.textContent = msg;
  el.style.color = color || '#666';
}

function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function sats(n) {
  return Number(n).toLocaleString();
}

function showError(err) {
  document.getElementById('results-pane').innerHTML = `
    <div class="err-block">
      <div class="ec">${esc(err.code)}</div>
      <div style="margin-top:6px;color:#a05050">${esc(err.message)}</div>
    </div>`;
}

function warnText(code) {
  return {
    HIGH_FEE:      'HIGH FEE — exceeds 1,000,000 sats or 200 sat/vB',
    SEND_ALL:      'SEND ALL — no change output; all leftover absorbed as fee',
    RBF_SIGNALING: 'RBF SIGNALING — transaction opts into Replace-By-Fee (BIP-125)',
    DUST_CHANGE:   'DUST CHANGE — change output below 546 sat threshold',
  }[code] || code;
}

function renderResult(d) {
  const totalIn  = d.selected_inputs.reduce((s,u)=>s+u.value_sats, 0);
  const payments = d.outputs.filter(o=>!o.is_change);
  const change   = d.outputs.find(o=>o.is_change);
  const totalOut = d.outputs.reduce((s,o)=>s+o.value_sats, 0);

  // Flow bar
  const flowHtml = `
    <div class="flow-bar">
      <div class="flow-cell">
        <div class="fc-val">${d.selected_inputs.length}</div>
        <div class="fc-lbl">Input${d.selected_inputs.length>1?'s':''}</div>
      </div>
      <div class="flow-cell">
        <div class="fc-val">${payments.length}</div>
        <div class="fc-lbl">Payment${payments.length>1?'s':''}</div>
      </div>
      ${change ? `<div class="flow-cell chg">
        <div class="fc-val">1</div>
        <div class="fc-lbl">Change</div>
      </div>` : ''}
      <div class="flow-cell fee">
        <div class="fc-val">${sats(d.fee_sats)}</div>
        <div class="fc-lbl">Fee (sats)</div>
      </div>
    </div>`;

  // Stats
  const statsHtml = `
    <div class="stats-grid">
      <div class="stat-cell"><div class="sk">Network</div><div class="sv amber">${esc(d.network)}</div></div>
      <div class="stat-cell"><div class="sk">Strategy</div><div class="sv dim">${esc(d.strategy)}</div></div>
      <div class="stat-cell"><div class="sk">Total Input</div><div class="sv">${sats(totalIn)} sat</div></div>
      <div class="stat-cell"><div class="sk">Total Output</div><div class="sv">${sats(totalOut)} sat</div></div>
      <div class="stat-cell"><div class="sk">Fee</div><div class="sv amber">${sats(d.fee_sats)} sat</div></div>
      <div class="stat-cell"><div class="sk">Fee Rate</div><div class="sv">${d.fee_rate_sat_vb} sat/vB</div></div>
      <div class="stat-cell"><div class="sk">Size</div><div class="sv">${d.vbytes} vbytes</div></div>
      <div class="stat-cell"><div class="sk">Change Index</div><div class="sv ${d.change_index!==null?'blue':'dim'}">${d.change_index !== null ? '#'+d.change_index : '—'}</div></div>
    </div>`;

  // RBF + Locktime
  const rbfClass = d.rbf_signaling ? 'rbf-on' : 'rbf-off';
  const rbfText  = d.rbf_signaling ? 'RBF ON &nbsp;nSeq=0xFFFFFFFD' : 'RBF OFF &nbsp;nSeq=0xFFFFFFFF';
  const ltClass  = d.locktime > 0 ? 'lt-chip' : 'lt-chip lt-none';
  const ltText   = d.locktime > 0
    ? `nLockTime ${d.locktime} &nbsp;<span style="color:#444">(${d.locktime_type})</span>`
    : `nLockTime 0 &nbsp;<span style="color:#333">(none)</span>`;

  const rbfHtml = `
    <div class="section">
      <div class="sec-head">// RBF &amp; LOCKTIME</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <div class="${rbfClass} rbf-pill"><span class="rbf-dot"></span>${rbfText}</div>
        <div class="${ltClass}">${ltText}</div>
      </div>
    </div>`;

  // Inputs
  const inputsHtml = `
    <div class="section">
      <div class="sec-head">// SELECTED INPUTS <span class="count">${d.selected_inputs.length}</span></div>
      ${d.selected_inputs.map(u=>`
        <div class="tx-row">
          <div>
            <div class="txid">${esc(u.txid.slice(0,32))}…<span class="vout">:${u.vout}</span></div>
            <div style="margin-top:4px">
              <span class="tag tag-type">${esc(u.script_type)}</span>
            </div>
            ${u.address ? `<div class="addr">${esc(u.address)}</div>` : ''}
          </div>
          <div class="amount">${sats(u.value_sats)}<br><span class="unit">sat</span></div>
        </div>`).join('')}
    </div>`;

  // Outputs
  const outputsHtml = `
    <div class="section">
      <div class="sec-head">// OUTPUTS <span class="count">${d.outputs.length}</span>
        ${d.change_index!==null?`<span style="color:#5b9bd5;font-size:9px">change @ #${d.change_index}</span>`:''}
      </div>
      ${d.outputs.map(o=>`
        <div class="tx-row">
          <div>
            <span class="tag ${o.is_change?'tag-change':'tag-payment'}">${o.is_change?'CHANGE':'PAYMENT'}</span>
            <span class="tag tag-type">${esc(o.script_type)}</span>
            ${o.address ? `<div class="addr" style="margin-top:4px">${esc(o.address)}</div>` : ''}
          </div>
          <div class="amount">${sats(o.value_sats)}<br><span class="unit">sat</span></div>
        </div>`).join('')}
    </div>`;

  // Warnings
  const warningsHtml = `
    <div class="section">
      <div class="sec-head">// WARNINGS <span class="count">${d.warnings.length}</span></div>
      ${d.warnings.length === 0
        ? '<div style="font-size:10px;color:#4caf7d">&#10003; NO WARNINGS</div>'
        : d.warnings.map(w=>`
            <div class="warn-row w-${w.code}">${warnText(w.code)}</div>`).join('')}
    </div>`;

  // PSBT
  const psbtHtml = `
    <div class="section">
      <div class="sec-head">// PSBT BASE64</div>
      <div class="psbt-wrap">
  <div class="psbt-text" id="psbt-data">${esc(d.psbt_base64)}</div>
  <button class="psbt-copy" onclick="copyPsbt()">
    <span class="copy-icon">&#x2398;</span> COPY TO CLIPBOARD
  </button>
</div>
    </div>`;

  document.getElementById('results-pane').innerHTML =
    flowHtml + statsHtml + rbfHtml + inputsHtml + outputsHtml + warningsHtml + psbtHtml;

  window._psbt = d.psbt_base64;
}

function copyPsbt() {
  if (!window._psbt) return;
  navigator.clipboard.writeText(window._psbt).then(() => {
    setStatus('> psbt copied to clipboard', '#4caf7d');
    const btn = document.querySelector('.psbt-copy');
    if (btn) {
      const orig = btn.innerHTML;
      btn.innerHTML = '<span class="copy-icon">&#x2713;</span> COPIED';
      btn.style.color = '#4caf7d';
      btn.style.borderTopColor = '#2a4a36';
      setTimeout(() => {
        btn.innerHTML = orig;
        btn.style.color = '';
        btn.style.borderTopColor = '';
      }, 2000);
    }
  });
}

function clearAll() {
  document.getElementById('fixture-input').value = '';
  const sl = document.getElementById('status-line');
  sl.textContent = '';
  sl.style.color = '';
  document.getElementById('results-pane').innerHTML = `
    <div class="empty">
      <div class="empty-glyph">₿</div>
      <div class="empty-text">AWAITING FIXTURE INPUT</div>
    </div>`;
  window._psbt = null;
}
</script>
</body>
</html>"""


@app.route('/')
def index():
    return render_template_string(HTML)


@app.route('/api/health')
def health():
    return jsonify({'ok': True})


@app.route('/api/build', methods=['POST'])
def build():
    try:
        fixture = request.get_json(force=True, silent=True)
        if fixture is None:
            return (
                jsonify(
                    {
                        'ok': False,
                        'error': {
                            'code': 'INVALID_FIXTURE',
                            'message': 'Request body is not valid JSON'
                        }
                    }
                ),
                400
            )

        validate_fixture(fixture)
        report = select_coins(fixture)
        return jsonify(report)

    except ValueError as e:
        msg = str(e)
        if ': ' in msg:
            code, message = msg.split(': ', 1)
        else:
            code, message = 'BUILD_ERROR', msg
        return jsonify({'ok': False, 'error': {'code': code, 'message': message}}), 400

    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return (
            jsonify(
                {'ok': False, 'error': {'code': 'INTERNAL_ERROR', 'message': str(e)}}
            ),
            500
        )


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    print(f'http://127.0.0.1:{port}', flush=True)
    app.run(host='0.0.0.0', port=port, debug=False)
