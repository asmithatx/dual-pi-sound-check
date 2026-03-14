/**
 * dashboard.js — SoundCheck PWA client
 * Handles Socket.IO connection, progress updates, gauge rendering, history.
 */

'use strict';

// ── Socket.IO ──────────────────────────────────────────────────────────────
const socket = io({ transports: ['websocket'], reconnectionDelay: 2000 });

socket.on('connect', () => {
  console.log('Socket connected');
  setConnected(true);
  loadHistory();
});
socket.on('disconnect', () => {
  console.log('Socket disconnected');
  setConnected(false);
});
socket.on('check_progress', onProgress);
socket.on('check_complete', onComplete);
socket.on('check_error',    onError);

// ── State ──────────────────────────────────────────────────────────────────
let checkRunning  = false;
let wakeLock      = null;
let thresholds    = { red_db: -25, amber_db: -40 };

// Load thresholds from server
fetch('/api/config')
  .then(r => r.json())
  .then(d => { thresholds = d; })
  .catch(() => {});

// ── Connection badge ───────────────────────────────────────────────────────
function setConnected(yes) {
  const badge = document.getElementById('conn-badge');
  if (yes) {
    badge.innerHTML = '<i class="bi bi-wifi me-1"></i>Connected';
    badge.className = 'badge connected';
  } else {
    badge.innerHTML = '<i class="bi bi-wifi-off me-1"></i>Offline';
    badge.className = 'badge bg-secondary';
  }
}

// ── Start a check ──────────────────────────────────────────────────────────
function startCheck(mode) {
  if (checkRunning) return;
  checkRunning = true;

  // Acquire wake lock (keeps screen on during long check)
  if ('wakeLock' in navigator) {
    navigator.wakeLock.request('screen')
      .then(wl => { wakeLock = wl; })
      .catch(() => {});
  }

  hideAll();
  showSection('status-section');
  setStatus('Sending command…', 2);
  setProgress(2);
  setModeBtnsDisabled(true);
  document.getElementById('btn-cancel').classList.remove('d-none');

  socket.emit('start_check', { mode });
}

function cancelCheck() {
  // No mid-check cancel on the server side — just reset UI
  resetUI();
}

// ── Progress handler ───────────────────────────────────────────────────────
function onProgress(data) {
  const { phase, elapsed, duration, progress } = data;

  if (phase === 'recording') {
    const remaining = duration - elapsed;
    setStatus(`Recording… ${elapsed}/${duration}s`, progress);
    setProgress(progress);
    document.getElementById('time-remaining').textContent =
      `${remaining}s left`;
  }
  else if (phase === 'waiting_for_remote') {
    setStatus('Waiting for bedroom Pi…', 100);
    setProgress(100, 'striped');
    document.getElementById('time-remaining').textContent = '';
  }
  else if (phase === 'analysing') {
    setStatus('Analysing…', 100);
    setProgress(100, 'striped');
  }
}

// ── Complete handler ───────────────────────────────────────────────────────
function onComplete(data) {
  checkRunning = false;
  releaseWakeLock();
  document.getElementById('btn-cancel').classList.add('d-none');

  const {
    leakage_db, confidence, result_color,
    bass_db, mid_db, treble_db,
    mode, n_segments
  } = data;

  hideAll();
  showSection('result-section');
  renderResult(leakage_db, result_color, confidence,
               bass_db, mid_db, treble_db, mode, n_segments);
  setModeBtnsDisabled(false);

  // Haptic feedback on Android
  if ('vibrate' in navigator) {
    navigator.vibrate(result_color === 'green' ? [100] : [100, 50, 200]);
  }

  loadHistory();
}

// ── Error handler ──────────────────────────────────────────────────────────
function onError(data) {
  checkRunning = false;
  releaseWakeLock();
  document.getElementById('btn-cancel').classList.add('d-none');

  hideAll();
  showSection('mode-section');
  const err = document.getElementById('error-alert');
  err.classList.remove('d-none');
  document.getElementById('error-text').textContent =
    data.message || 'Unknown error';
  setModeBtnsDisabled(false);

  setTimeout(() => err.classList.add('d-none'), 8000);
}

// ── Result rendering ───────────────────────────────────────────────────────
function renderResult(db, color, confidence, bass, mid, treble, mode, segs) {
  // Gauge
  drawGauge(db, color);

  // DB readout
  const dbEl = document.getElementById('gauge-db');
  dbEl.textContent = db.toFixed(1);
  dbEl.style.color = colorHex(color);

  // Verdict badge
  const badge = document.getElementById('verdict-badge');
  const verdictText = { green: '✓ Safe', amber: '⚠ Borderline', red: '✕ Too Loud' };
  badge.textContent  = verdictText[color] || color;
  badge.className    = `verdict-badge verdict-${color}`;

  // Confidence
  document.getElementById('confidence-text').textContent =
    `${capitalize(confidence)} confidence · ${segs.toLocaleString()} FFT segments`;

  // Result card glow
  const card = document.getElementById('result-card');
  card.className = `card card-dark result-card result-${color}`;

  // Band bars
  renderBand('bass',   bass);
  renderBand('mid',    mid);
  renderBand('treble', treble);

  // Meta
  document.getElementById('result-mode').textContent =
    `${capitalize(mode)} check`;
  document.getElementById('result-segments').textContent =
    `${segs.toLocaleString()} segments`;
}

function renderBand(name, db) {
  const bar = document.getElementById(`bar-${name}`);
  const val = document.getElementById(`val-${name}`);
  val.textContent = db.toFixed(1);

  // Map db range [-80, 0] to width 0–100%
  const pct = Math.max(0, Math.min(100, (db + 80) / 80 * 100));
  bar.style.width = pct + '%';

  const bColor = db > thresholds.red_db   ? '#ef4444'
               : db > thresholds.amber_db ? '#f59e0b'
               :                            '#22c55e';
  bar.style.background = bColor;
  val.style.color      = bColor;
}

// ── Gauge canvas (half-doughnut) ───────────────────────────────────────────
function drawGauge(db, color) {
  const canvas = document.getElementById('gauge-canvas');
  const ctx    = canvas.getContext('2d');
  const W = canvas.width;
  const H = canvas.height;
  const cx = W / 2;
  const cy = H - 20;
  const r  = Math.min(W, H * 2) / 2 - 16;

  ctx.clearRect(0, 0, W, H);

  // Range: -80 dB (quiet) to 0 dB (loud)
  const minDb = -80, maxDb = 0;
  const startAngle = Math.PI;                   // 180° (left)
  const endAngle   = 2 * Math.PI;              // 360° (right)
  const totalArc   = endAngle - startAngle;

  // Track segments (green / amber / red zones)
  const zones = [
    { from: minDb, to: thresholds.amber_db, color: '#22c55e33' },
    { from: thresholds.amber_db, to: thresholds.red_db, color: '#f59e0b33' },
    { from: thresholds.red_db, to: maxDb,   color: '#ef444433' },
  ];
  zones.forEach(z => {
    const a1 = startAngle + (z.from - minDb) / (maxDb - minDb) * totalArc;
    const a2 = startAngle + (z.to   - minDb) / (maxDb - minDb) * totalArc;
    ctx.beginPath();
    ctx.arc(cx, cy, r, a1, a2);
    ctx.lineWidth = 18;
    ctx.strokeStyle = z.color;
    ctx.stroke();
  });

  // Active arc (from min to current value)
  const valueAngle = startAngle +
    Math.max(0, Math.min(1, (db - minDb) / (maxDb - minDb))) * totalArc;
  const grad = ctx.createLinearGradient(cx - r, cy, cx + r, cy);
  grad.addColorStop(0,   '#22c55e');
  grad.addColorStop(0.5, '#f59e0b');
  grad.addColorStop(1,   '#ef4444');
  ctx.beginPath();
  ctx.arc(cx, cy, r, startAngle, valueAngle);
  ctx.lineWidth    = 18;
  ctx.strokeStyle  = colorHex(color);
  ctx.lineCap      = 'round';
  ctx.stroke();

  // Needle
  ctx.beginPath();
  ctx.arc(cx, cy, r, startAngle, valueAngle);
  const nx = cx + r * Math.cos(valueAngle);
  const ny = cy + r * Math.sin(valueAngle);
  ctx.beginPath();
  ctx.arc(nx, ny, 6, 0, 2 * Math.PI);
  ctx.fillStyle = colorHex(color);
  ctx.fill();

  // Scale ticks
  [-80, -60, -40, -25, 0].forEach(tick => {
    const angle = startAngle + (tick - minDb) / (maxDb - minDb) * totalArc;
    const ix = cx + (r + 14) * Math.cos(angle);
    const iy = cy + (r + 14) * Math.sin(angle);
    ctx.fillStyle    = '#4b5563';
    ctx.font         = '10px monospace';
    ctx.textAlign    = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(tick, ix, iy);
  });
}

// ── History ────────────────────────────────────────────────────────────────
function loadHistory() {
  fetch('/api/history')
    .then(r => r.json())
    .then(renderHistory)
    .catch(() => {});
}

function renderHistory(rows) {
  const el = document.getElementById('history-list');
  if (!rows || rows.length === 0) {
    el.innerHTML = '<p class="text-muted small text-center py-2">No checks yet</p>';
    return;
  }
  el.innerHTML = rows.map(row => {
    const color = row.result_color || 'green';
    const dt    = new Date(row.timestamp * 1000);
    const time  = dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const date  = dt.toLocaleDateString([], { month: 'short', day: 'numeric' });
    return `
      <div class="history-item c-${color}">
        <div class="history-db" style="color:${colorHex(color)}">
          ${(row.overall_leakage_db || 0).toFixed(1)}
        </div>
        <div class="history-meta">
          <div class="history-mode">${capitalize(row.mode || 'check')}</div>
          <div class="history-time">${date} ${time}</div>
        </div>
        <div class="history-conf">
          ${capitalize(row.confidence || '')}<br>
          <span style="color:${colorHex(color)}">●</span>
        </div>
      </div>`;
  }).join('');
}

// ── UI helpers ─────────────────────────────────────────────────────────────
function setStatus(text, progress) {
  document.getElementById('status-text').textContent = text;
}

function setProgress(pct, style = 'default') {
  const bar = document.getElementById('progress-bar');
  bar.style.width = pct + '%';
  bar.setAttribute('aria-valuenow', pct);
  document.getElementById('progress-label').textContent = pct + '%';
  if (style === 'striped') {
    bar.className =
      'progress-bar bg-warning progress-bar-striped progress-bar-animated';
  } else {
    bar.className = 'progress-bar bg-primary progress-bar-striped progress-bar-animated';
  }
}

function hideAll() {
  ['mode-section', 'status-section', 'result-section', 'error-alert']
    .forEach(id => document.getElementById(id).classList.add('d-none'));
}

function showSection(id) {
  document.getElementById(id).classList.remove('d-none');
}

function setModeBtnsDisabled(disabled) {
  ['btn-short', 'btn-medium', 'btn-long'].forEach(id => {
    const el = document.getElementById(id);
    if (disabled) el.setAttribute('disabled', '');
    else          el.removeAttribute('disabled');
  });
  if (!disabled) {
    showSection('mode-section');
  }
}

function resetUI() {
  checkRunning = false;
  releaseWakeLock();
  hideAll();
  showSection('mode-section');
  setModeBtnsDisabled(false);
}

function releaseWakeLock() {
  if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
}

function colorHex(color) {
  return color === 'green' ? '#22c55e'
       : color === 'amber' ? '#f59e0b'
       : color === 'red'   ? '#ef4444'
       :                     '#9ca3af';
}

function capitalize(s) {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : '';
}

// ── Service worker registration ────────────────────────────────────────────
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js')
    .then(() => console.log('SW registered'))
    .catch(e  => console.warn('SW registration failed:', e));
}
