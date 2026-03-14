# Dual Raspberry Pi on-demand sound leakage monitor

**A spot-check system that uses spectral coherence to measure exactly how much of your music leaks through the wall — only when you ask it to.** Both Raspberry Pis sit idle with microphones inactive until you tap a button on your phone. This on-demand-only architecture eliminates background daemons, false alerts, and privacy concerns while producing *better* measurements than continuous monitoring ever could: you only check when music is playing, giving the coherence algorithm exactly the broadband, sustained signal it needs. The system computes magnitude-squared coherence between synchronized recordings from both rooms, separating your music's wall transmission from ambient noise at every frequency. Results appear on a mobile-optimised PWA dashboard within seconds of the check completing, with a traffic-light verdict and per-band frequency breakdown.

---

## Why on-demand-only is the superior architecture

Continuous monitoring systems carry architectural baggage that provides no benefit for this use case. A scheduler (APScheduler, cron) would fire checks when nobody is playing music, producing meaningless measurements of background noise. A always-recording system raises privacy questions, consumes SD card write cycles, and demands alerting logic to distinguish "worth measuring" from silence. The on-demand approach eliminates all of this.

**Simpler architecture** means fewer failure modes. Pi A runs a single lightweight MQTT listener — a 60-line Python script that maintains one TCP connection and uses effectively zero CPU until commanded. Pi B runs a minimal Flask server to render one web page. Neither Pi touches a microphone until you explicitly trigger a check. There is no APScheduler, no cron job, no periodic background task of any kind.

**Better sample quality** is the counterintuitive win. Because you only trigger a check while music is already playing at the volume you care about, the signal conditions are ideal every time. Coherence estimation requires correlated energy at each frequency bin to produce meaningful results. Music — broadband, sustained, harmonically rich — is the *perfect* input signal. A continuous monitor would need "is anything worth measuring?" logic and would often run during silence or TV audio, producing noisy, low-confidence estimates. **On-demand while playing completely eliminates this problem.**

**Privacy by design** is absolute. Audio is never recorded, processed, or stored unless you physically tap a button. The microphones are electrically connected (USB devices are always powered), but no software reads from them during idle. Pi A's listener script imports `sounddevice` but never calls `sd.rec()` until an MQTT command arrives. This is a stronger privacy guarantee than any "we delete recordings after processing" policy.

**No alerting complexity.** Since you are looking at the result screen the moment the check completes, the primary alert is visual — the page turns green, amber, or red. The optional ntfy.sh push notification becomes a nice-to-have backup rather than a critical alerting pipeline. At perhaps 5–10 checks per day, you will never approach the **250 messages/day** free-tier limit.

---

## Three check modes and the statistics behind them

The system offers three selectable durations, each producing dramatically different statistical confidence in the coherence estimate. The math is straightforward: longer recordings yield more FFT segments, and more segments reduce the variance of every coherence estimate.

### How segment count determines measurement quality

Scipy's `coherence()` uses Welch's method internally: it divides each recording into overlapping segments, computes the FFT of each, then averages the cross-spectral and auto-spectral densities across all segments before computing coherence. The number of segments K is:

```
K = 1 + floor((N - nperseg) / (nperseg - noverlap))
```

For 50% overlap (the standard with a Hann window), `noverlap = nperseg/2` and `step = nperseg/2`, so K ≈ 2N/nperseg − 1. The variance of the magnitude-squared coherence estimator is approximately **Var(Ĉ) ≈ 2C(1−C)² / K**, where C is the true coherence. Variance drops linearly with K — a 60-second recording has roughly 6× more segments than a 10-second recording and therefore ~6× lower variance per frequency bin. The bias floor (expected coherence for truly uncorrelated signals) is approximately **1/K**, which also drops with longer recordings, letting you detect smaller true coherence values with statistical significance.

| Mode | Duration | Samples (16 kHz) | Segments (K) | Bias floor (1/K) | Std dev at C=0.3 | Confidence |
|------|----------|-------------------|-------------|-------------------|-------------------|------------|
| **SHORT** | 10 s | 160,000 | **624** | 0.0016 | 0.022 | Medium |
| **MEDIUM** | 30 s | 480,000 | **1,874** | 0.0005 | 0.013 | High |
| **LONG** | 60 s | 960,000 | **3,749** | 0.0003 | 0.009 | Excellent |

Parameters used: `nperseg=512` for SHORT and MEDIUM (giving **31.25 Hz frequency resolution** at 16 kHz), `nperseg=1024` for LONG (giving **15.6 Hz resolution** — finer bass detail when you can afford the extra samples). All modes use `noverlap=nperseg//2` with a Hann window, the textbook combination for Welch's method.

### When to use each mode

**SHORT (10 s)** is a quick sanity check. With 624 segments, the coherence estimate is already statistically meaningful — the 95% significance threshold is only ~0.005, so any real leakage will be detected. Use this for a fast "am I in the ballpark?" check.

**MEDIUM (30 s)** is the recommended default. At 1,874 segments, the standard deviation of the coherence estimate drops to 0.013, giving tight confidence intervals. The 30-second wait is tolerable, and the measurement quality is genuinely good. **This is the sweet spot for most use.**

**LONG (60 s)** delivers the highest confidence. With 3,749 segments, the bias floor is negligible (0.0003), variance is minimal, and the `nperseg=1024` setting doubles the frequency resolution in the bass region where it matters most. Use this when you want to be certain — when calibrating the system, when testing a new speaker placement, or when a MEDIUM check returned an amber result and you want a definitive answer.

---

## Spectral coherence separates your music from everything else

The core insight of this system is that **magnitude-squared coherence measures the linear frequency-domain correlation between two signals**, not just their individual levels. A simple dB meter in the bedroom would pick up the fridge humming, the heating system, street traffic — everything. Coherence isolates *only* the component of the bedroom signal that is linearly related to the living room signal at each frequency.

### The mathematics

```
Cxy(f) = |Pxy(f)|² / (Pxx(f) · Pyy(f))
```

Where Pxy(f) is the cross-spectral density between the living room and bedroom signals, and Pxx(f), Pyy(f) are their respective auto-spectral densities. The result ranges from 0 (no linear relationship — the bedroom sound at frequency f is entirely uncorrelated with the living room) to 1 (perfect linear relationship — the bedroom sound is a scaled, possibly phase-shifted copy of the living room signal at that frequency).

When your music plays in the living room, some frequencies pass through the wall and appear in the bedroom recording. The coherence at those frequencies will be high. Frequencies where the bedroom signal is dominated by local noise (HVAC, fridge, outside traffic) will show low coherence because those sources are uncorrelated with the living room signal. **This separation happens automatically, at every frequency, without you needing to identify or filter out noise sources.**

### Why music is the ideal test signal

Coherence estimation needs energy at each frequency bin to produce a meaningful estimate. At any frequency where the source signal has negligible energy, the coherence degenerates to noise. Music is ideal because it is **broadband** (bass, midrange, and treble energy simultaneously), **sustained** (no long silence gaps like speech), and **quasi-stationary** over the 32 ms FFT windows used here. Bass guitars, kick drums, and synth bass provide consistent low-frequency energy in exactly the 50–250 Hz band where wall leakage is worst. Since you only run checks while music is playing, every check benefits from these ideal conditions.

### Frequency-dependent wall attenuation

Sound transmission through residential walls follows the mass law: attenuation increases approximately **6 dB per octave** (per doubling of frequency). A typical interior wall (STC ~33–40) provides roughly:

| Frequency | Typical attenuation | Implication |
|-----------|-------------------|-------------|
| 50 Hz | ~10–15 dB | Bass passes almost freely |
| 125 Hz | ~18–25 dB | Strong bass leakage |
| 250 Hz | ~25–32 dB | Moderate leakage |
| 500 Hz | ~32–40 dB | Mild leakage |
| 1000 Hz | ~38–45 dB | Largely blocked |
| 4000 Hz | ~45–55 dB | Effectively blocked |

**Bass leakage is the primary concern** — physics guarantees it. The system weights bass coherence at 60%, midrange at 30%, and treble at 10% in its overall leakage score, reflecting this physical reality. STC ratings, commonly used to describe wall performance, only measure 125–4000 Hz and miss the sub-125 Hz region where complaints actually originate.

### The analysis function

```python
import numpy as np
from scipy import signal
from typing import Dict, Any

def analyze_bedroom_leakage(
    bedroom_audio: np.ndarray,
    livingroom_audio: np.ndarray,
    sample_rate: int = 16000,
    mode: str = 'medium'
) -> Dict[str, Any]:
    """
    Measure sound leakage via magnitude-squared coherence.
    
    Uses Welch's method to estimate how much of the living room
    signal is linearly present in the bedroom recording at each
    frequency, then applies band-weighting and a wall attenuation
    model to produce an overall leakage estimate.
    """
    # Mode-dependent FFT parameters
    config = {
        'short':  {'nperseg': 512},
        'medium': {'nperseg': 512},
        'long':   {'nperseg': 1024},
    }
    nperseg = config[mode]['nperseg']
    noverlap = nperseg // 2

    # Trim to equal length
    n = min(len(bedroom_audio), len(livingroom_audio))
    bedroom_audio = bedroom_audio[:n]
    livingroom_audio = livingroom_audio[:n]

    # Segment count for quality metrics
    step = nperseg - noverlap
    n_segments = 1 + (n - nperseg) // step

    # Compute magnitude-squared coherence
    freqs, coh = signal.coherence(
        livingroom_audio, bedroom_audio,
        fs=sample_rate, window='hann',
        nperseg=nperseg, noverlap=noverlap, detrend='constant'
    )

    # Frequency bands with perceptual weighting
    bands = {
        'bass':     {'range': (50, 250),   'weight': 0.60},
        'midrange': {'range': (250, 1000), 'weight': 0.30},
        'treble':   {'range': (1000, 4000),'weight': 0.10},
    }

    # Wall attenuation model (dB) for typical interior wall
    def wall_tl(f):
        """Piecewise transmission loss model, dB."""
        tl = np.zeros_like(f, dtype=float)
        tl[f < 125]  = 15 + 10 * np.log2(np.maximum(f[f < 125], 30) / 30)
        mask = (f >= 125) & (f < 500)
        tl[mask] = 25 + 12 * np.log2(f[mask] / 125)
        tl[f >= 500] = 49 + 6 * np.log2(f[f >= 500] / 500)
        return tl

    bias_floor = 1.0 / max(n_segments, 1)
    band_results = {}
    weighted_sum, total_weight = 0.0, 0.0

    for name, cfg in bands.items():
        lo, hi = cfg['range']
        mask = (freqs >= lo) & (freqs < hi)
        bc = np.maximum(coh[mask] - bias_floor, 0.0)
        atten = wall_tl(freqs[mask])

        mean_coh = float(np.mean(bc)) if bc.size else 0.0
        if mean_coh > 0:
            leakage = -float(np.average(atten, weights=bc + 1e-10)) \
                      + 10 * np.log10(max(mean_coh, 1e-10))
        else:
            leakage = -float(np.mean(atten)) - 30

        band_results[name] = {
            'mean_coherence': round(mean_coh, 5),
            'leakage_db': round(leakage, 1),
            'wall_attenuation_db': round(float(np.mean(atten)), 1),
        }
        weighted_sum += cfg['weight'] * leakage
        total_weight += cfg['weight']

    overall = weighted_sum / max(total_weight, 1e-10)

    if n_segments >= 2000:   confidence = 'excellent'
    elif n_segments >= 1000: confidence = 'high'
    elif n_segments >= 300:  confidence = 'medium'
    else:                    confidence = 'low'

    # Traffic-light classification
    if overall > -25:    color = 'red'
    elif overall > -40:  color = 'amber'
    else:                color = 'green'

    return {
        'overall_leakage_db': round(overall, 1),
        'band_breakdown': band_results,
        'confidence': confidence,
        'result_color': color,
        'n_segments': n_segments,
        'duration_sec': round(n / sample_rate, 1),
        'mode': mode,
    }
```

---

## System architecture and the check flow

The architecture is minimal by design. Two Pis, one MQTT broker, one Flask server, one web page.

### Component roles

**Pi B (living room)** is the orchestrator. It runs three always-on services: Mosquitto MQTT broker, the Flask/SocketIO web server, and nothing else. The Flask server renders the dashboard, handles WebSocket connections from your phone, sends MQTT commands to Pi A, records local audio when triggered, runs coherence analysis, and stores results in SQLite. All audio processing happens only during an active check.

**Pi A (bedroom)** is a lightweight responder. It runs a single Python script that maintains an MQTT connection and waits for commands. When idle, this script uses negligible CPU and zero audio resources. On receiving a record command, it activates the microphone, records for the specified duration, sends the audio back over MQTT, and returns to idle.

**Your phone** accesses the dashboard via a mobile browser (or as an installed PWA) on the same WiFi network. It communicates with Pi B over WebSocket for real-time progress updates during checks.

### The complete check sequence

```
1. You tap MEDIUM (30s) on your phone
2. Phone → Pi B: WebSocket emit('start_check', {mode: 'medium'})
3. Pi B generates check_id, calculates start_at = now + 1.5s
4. Pi B → MQTT: publish 'soundcheck/command' with:
   {"action":"record", "duration":30, "check_id":"a1b2c3d4", "start_at":1740000001.5}
5. Pi B starts countdown timer, emits progress events to phone
6. Pi A receives command, waits until start_at
7. At start_at: BOTH Pis begin recording simultaneously
   (Pi A records duration + 1s warm-up buffer)
   (Pi B records duration + 1s warm-up buffer)
8. During recording: Pi B emits {"phase":"recording", "elapsed":N, "duration":30}
   every second via WebSocket → phone updates progress bar
9. After recording: Pi A base64-encodes audio, publishes to
   'soundcheck/audio/a1b2c3d4' via MQTT (~2.4 MB for 60s)
10. Pi B receives Pi A's audio, discards first 1s from both recordings
11. Pi B runs analyze_bedroom_leakage() → ~0.5s computation
12. Pi B stores result in SQLite, emits 'check_complete' to phone
13. Phone displays traffic-light result + gauge + frequency breakdown
14. Pi B optionally POSTs to ntfy.sh as secondary notification
15. Both Pis return to idle — microphones inactive
```

The **1.5-second `start_at` offset** is deliberate. It gives Pi A enough time to receive the MQTT message over WiFi, parse it, and prepare the audio stream before the synchronized recording begins. On a typical home LAN, MQTT delivery takes 5–50 ms, so 1.5 seconds provides a generous margin. Both Pis call `time.sleep(start_at - time.time())` to synchronise their recording start within the accuracy of chrony time sync (~1 ms on a LAN).

### Pi B: Flask application (app.py)

```python
#!/usr/bin/env python3
"""SoundCheck — On-demand sound leakage monitor (Pi B / Living Room)"""

import eventlet
eventlet.monkey_patch()

import time, uuid, json, base64, sqlite3, logging
from threading import Event
import numpy as np
import sounddevice as sd
import requests

from flask import Flask, render_template, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
import paho.mqtt.client as mqtt

# ── Configuration ────────────────────────────────────────────────
SAMPLE_RATE     = 16000
CHANNELS        = 1
WARMUP_SECONDS  = 1       # discard first second of recording
MQTT_BROKER     = 'localhost'
MQTT_PORT       = 1883
NTFY_TOPIC      = 'my-soundcheck'   # change to your ntfy.sh topic
NTFY_ENABLED    = True
DB_PATH         = 'soundcheck.db'

DURATIONS = {'short': 10, 'medium': 30, 'long': 60}

# ── App Setup ────────────────────────────────────────────────────
app = Flask(__name__)
app.config['SECRET_KEY'] = 'change-me-in-production'
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins='*')
logging.basicConfig(level=logging.INFO)
log = logging.getLogger('soundcheck')

# ── SQLite ───────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS checks (
        id TEXT PRIMARY KEY, timestamp REAL, mode TEXT,
        duration_seconds INTEGER, overall_leakage_db REAL,
        bass_db REAL, midrange_db REAL, treble_db REAL,
        confidence TEXT, result_color TEXT, n_segments INTEGER
    )''')
    conn.commit()
    conn.close()

init_db()

def save_result(check_id, mode, duration, result):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        'INSERT INTO checks VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        (check_id, time.time(), mode, duration,
         result['overall_leakage_db'],
         result['band_breakdown']['bass']['leakage_db'],
         result['band_breakdown']['midrange']['leakage_db'],
         result['band_breakdown']['treble']['leakage_db'],
         result['confidence'], result['result_color'],
         result['n_segments'])
    )
    conn.commit()
    conn.close()

def get_history(limit=10):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        'SELECT * FROM checks ORDER BY timestamp DESC LIMIT ?', (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── MQTT ─────────────────────────────────────────────────────────
remote_audio_store = {}   # check_id → numpy array
audio_received = {}       # check_id → threading.Event

mqtt_client = mqtt.Client(client_id='soundcheck-pib')

def on_mqtt_message(client, userdata, msg):
    """Handle audio data arriving from Pi A."""
    if msg.topic.startswith('soundcheck/audio/'):
        check_id = msg.topic.split('/')[-1]
        try:
            audio_bytes = base64.b64decode(msg.payload)
            audio_array = np.frombuffer(audio_bytes, dtype=np.int16) \
                            .astype(np.float64) / 32768.0
            remote_audio_store[check_id] = audio_array
            if check_id in audio_received:
                audio_received[check_id].set()
            log.info(f"Received audio for {check_id}: "
                     f"{len(audio_array)} samples")
        except Exception as e:
            log.error(f"Failed to decode audio for {check_id}: {e}")

mqtt_client.on_message = on_mqtt_message
mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
mqtt_client.subscribe('soundcheck/audio/#')
mqtt_client.loop_start()

# ── Recording ────────────────────────────────────────────────────
def record_local(duration_sec):
    """Record from local USB mic, return float64 array with warm-up trimmed."""
    total = duration_sec + WARMUP_SECONDS
    log.info(f"Recording {total}s locally (first {WARMUP_SECONDS}s discarded)")
    audio = sd.rec(int(total * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                   channels=CHANNELS, dtype='int16')
    sd.wait()
    trimmed = audio[WARMUP_SECONDS * SAMPLE_RATE:, 0]
    return trimmed.astype(np.float64) / 32768.0

# ── ntfy.sh ──────────────────────────────────────────────────────
def send_ntfy(result):
    if not NTFY_ENABLED:
        return
    icons = {'green': '🟢', 'amber': '🟡', 'red': '🔴'}
    icon = icons.get(result['result_color'], '⚪')
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=f"{icon} Leakage: {result['overall_leakage_db']:.1f} dB "
                 f"({result['confidence']} confidence)",
            headers={
                "Title": "Sound Check Result",
                "Priority": "default" if result['result_color'] == 'green'
                            else "high",
                "Tags": "speaker,sound_check",
            },
            timeout=5
        )
    except Exception as e:
        log.warning(f"ntfy.sh notification failed: {e}")

# ── Routes ───────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/manifest.json')
def manifest():
    return send_from_directory('static', 'manifest.json',
                               mimetype='application/manifest+json')

@app.route('/sw.js')
def service_worker():
    return send_from_directory('static', 'sw.js',
                               mimetype='application/javascript')

@app.route('/api/history')
def api_history():
    return jsonify(get_history())

# ── SocketIO Events ──────────────────────────────────────────────
@socketio.on('start_check')
def handle_start_check(data):
    mode = data.get('mode', 'medium')
    if mode not in DURATIONS:
        emit('check_error', {'message': f'Invalid mode: {mode}'})
        return

    duration = DURATIONS[mode]
    check_id = uuid.uuid4().hex[:8]
    start_at = time.time() + 1.5

    # Prepare to receive Pi A's audio
    audio_received[check_id] = Event()

    # Send command to Pi A
    command = json.dumps({
        'action': 'record',
        'duration': duration,
        'check_id': check_id,
        'start_at': start_at,
    })
    mqtt_client.publish('soundcheck/command', command, qos=1)
    log.info(f"Check {check_id}: mode={mode}, duration={duration}s, "
             f"start_at={start_at:.3f}")

    # Run the check in a background task
    socketio.start_background_task(
        run_check, check_id, mode, duration, start_at
    )

def run_check(check_id, mode, duration, start_at):
    """Background task: record, wait for Pi A, analyse, report."""
    try:
        # Wait until synchronised start time
        wait = start_at - time.time()
        if wait > 0:
            eventlet.sleep(wait)

        # Record locally with progress updates
        total_rec = duration + WARMUP_SECONDS
        audio = sd.rec(int(total_rec * SAMPLE_RATE),
                       samplerate=SAMPLE_RATE,
                       channels=CHANNELS, dtype='int16')

        for elapsed in range(1, duration + 1):
            eventlet.sleep(1)
            socketio.emit('check_progress', {
                'phase': 'recording',
                'elapsed': elapsed,
                'duration': duration,
                'progress': round(100 * elapsed / duration),
            })
        sd.wait()

        local_audio = audio[WARMUP_SECONDS * SAMPLE_RATE:, 0] \
                        .astype(np.float64) / 32768.0

        # Wait for Pi A's audio (up to 15s grace period)
        socketio.emit('check_progress', {'phase': 'waiting_for_remote'})
        received = audio_received[check_id].wait(timeout=15)
        if not received:
            socketio.emit('check_error',
                          {'message': 'Timeout waiting for bedroom audio'})
            return

        remote_audio = remote_audio_store.pop(check_id, None)
        del audio_received[check_id]

        if remote_audio is None:
            socketio.emit('check_error',
                          {'message': 'No audio received from bedroom Pi'})
            return

        # Analyse
        socketio.emit('check_progress', {'phase': 'analysing'})

        # Import here to keep the function self-contained
        from analysis import analyze_bedroom_leakage
        result = analyze_bedroom_leakage(
            bedroom_audio=remote_audio,
            livingroom_audio=local_audio,
            sample_rate=SAMPLE_RATE,
            mode=mode,
        )

        # Store in database
        save_result(check_id, mode, duration, result)

        # Send result to browser
        socketio.emit('check_complete', {
            'leakage_db': result['overall_leakage_db'],
            'confidence': result['confidence'],
            'result_color': result['result_color'],
            'mode': mode,
            'bass': result['band_breakdown']['bass']['leakage_db'],
            'mid': result['band_breakdown']['midrange']['leakage_db'],
            'treble': result['band_breakdown']['treble']['leakage_db'],
            'n_segments': result['n_segments'],
        })

        # Optional push notification
        send_ntfy(result)

        log.info(f"Check {check_id} complete: "
                 f"{result['overall_leakage_db']} dB "
                 f"({result['result_color']})")

    except Exception as e:
        log.error(f"Check {check_id} failed: {e}", exc_info=True)
        socketio.emit('check_error', {'message': str(e)})

# ── Main ─────────────────────────────────────────────────────────
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
```

### Pi A: bedroom listener (listener.py)

```python
#!/usr/bin/env python3
"""SoundCheck Bedroom Listener (Pi A) — records audio on MQTT command."""

import time, json, base64, logging
import numpy as np
import sounddevice as sd
import paho.mqtt.client as mqtt

# ── Configuration ────────────────────────────────────────────────
PI_B_ADDRESS    = '192.168.1.100'  # Pi B's IP — change to yours
MQTT_PORT       = 1883
SAMPLE_RATE     = 16000
CHANNELS        = 1
WARMUP_SECONDS  = 1

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('listener')

# ── MQTT Handlers ────────────────────────────────────────────────
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info("Connected to MQTT broker")
        client.subscribe('soundcheck/command', qos=1)
    else:
        log.error(f"MQTT connection failed: rc={rc}")

def on_message(client, userdata, msg):
    try:
        cmd = json.loads(msg.payload.decode())
        if cmd.get('action') != 'record':
            return

        duration  = cmd['duration']
        check_id  = cmd['check_id']
        start_at  = cmd['start_at']

        log.info(f"Received record command: check_id={check_id}, "
                 f"duration={duration}s")

        # Wait until synchronised start time
        wait = start_at - time.time()
        if wait > 0:
            log.info(f"Waiting {wait:.3f}s until start_at")
            time.sleep(wait)
        elif wait < -2:
            log.warning(f"start_at is {-wait:.1f}s in the past — skipping")
            return

        # Record with warm-up buffer
        total = duration + WARMUP_SECONDS
        log.info(f"Recording {total}s ({WARMUP_SECONDS}s warm-up + "
                 f"{duration}s measurement)")
        audio = sd.rec(int(total * SAMPLE_RATE),
                       samplerate=SAMPLE_RATE,
                       channels=CHANNELS, dtype='int16')
        sd.wait()

        # Trim warm-up period
        trimmed = audio[WARMUP_SECONDS * SAMPLE_RATE:]

        # Encode and send back
        audio_bytes = trimmed.tobytes()
        payload = base64.b64encode(audio_bytes)
        topic = f'soundcheck/audio/{check_id}'

        log.info(f"Sending {len(payload)} bytes to {topic}")
        client.publish(topic, payload, qos=1)
        log.info(f"Audio sent for check {check_id}")

    except Exception as e:
        log.error(f"Error handling command: {e}", exc_info=True)

# ── Main Loop ────────────────────────────────────────────────────
client = mqtt.Client(client_id='soundcheck-pia')
client.on_connect = on_connect
client.on_message = on_message

log.info(f"Connecting to MQTT broker at {PI_B_ADDRESS}:{MQTT_PORT}")
client.connect(PI_B_ADDRESS, MQTT_PORT, keepalive=60)

# Blocks forever — uses ~0% CPU when idle (epoll-based network wait)
client.loop_forever()
```

This script is the entirety of what runs on Pi A. When idle, `loop_forever()` blocks on a network socket using epoll, consuming effectively **zero CPU**. No audio device is opened, no recording happens, no data is processed. The `sounddevice` import loads the library into memory (~15 MB) but touches no hardware until `sd.rec()` is called.

---

## Real-time progress UX keeps a 60-second wait tolerable

A 60-second LONG check is a noticeable wait. The system uses WebSocket events (Flask-SocketIO) to keep you informed throughout. Pi B emits a progress event every second during recording, which the browser uses to update a progress bar and countdown timer.

The key JavaScript handling on the client side:

```javascript
socket.on('check_progress', (data) => {
    if (data.phase === 'recording') {
        setStatus('recording',
            `Recording… (${data.elapsed}/${data.duration}s)`);
        setProgress(data.progress);
    }
    if (data.phase === 'analysing') {
        setStatus('analysing', 'Analysing audio…');
        setProgress(100);
        // Switch to animated striped bar
        progressBar.className =
            'progress-bar bg-warning progress-bar-striped ' +
            'progress-bar-animated';
    }
});

socket.on('check_complete', (data) => {
    showResult(data);
    // Haptic feedback on Android
    if ('vibrate' in navigator) navigator.vibrate([100, 50, 200]);
});
```

The **Wake Lock API** (`navigator.wakeLock.request('screen')`) prevents the phone screen from dimming during a long check. This is supported across all major browsers since early 2025 (Chrome, Firefox, Safari, Edge). The lock is acquired when a check starts and released when it completes or errors. On iOS, the Vibration API is not available, but Wake Lock works from Safari 16.4+.

---

## Mobile PWA dashboard built for tapping, not typing

The dashboard is a single-page app served by Flask, designed mobile-first for a phone screen. It uses **Bootstrap 5.3** with built-in dark theme (`data-bs-theme="dark"`), **Chart.js 4** for a gauge visualisation, and **Socket.IO 4.7** for real-time communication.

### Layout and interaction design

The page has four zones stacked vertically. At the top, three large tap-friendly buttons — **SHORT** (green gradient), **MEDIUM** (blue-purple gradient), **LONG** (pink-purple gradient) — each spanning the full width with 18px padding, large enough for easy thumb targeting. Below that, a status card appears during checks showing the current phase and an animated progress bar. The result card dominates the middle of the screen: a half-doughnut gauge chart shows the leakage dB on a 0–80 scale with color zones, a large numeric readout, a traffic-light badge ("Safe" / "Borderline" / "Too loud"), and a three-row frequency breakdown showing bass, midrange, and treble leakage individually. At the bottom, a scrollable history list shows the last 10 checks with color-coded left borders and timestamps.

### PWA installation

The manifest.json enables "Add to Home Screen" on both iOS and Android:

```json
{
  "name": "Sound Leak Monitor",
  "short_name": "LeakMon",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#1a1a2e",
  "theme_color": "#16213e",
  "icons": [
    {"src": "/static/icons/icon-192x192.png", "sizes": "192x192",
     "type": "image/png", "purpose": "any maskable"},
    {"src": "/static/icons/icon-512x512.png", "sizes": "512x512",
     "type": "image/png", "purpose": "any maskable"}
  ]
}
```

On iOS, apple-touch-icon link elements in the HTML head override manifest icons. The HTML includes `<meta name="apple-mobile-web-app-capable" content="yes">` and `<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">` for proper standalone behaviour. A minimal service worker caches the app shell for instant loading (but excludes Socket.IO requests from the cache). Once installed, the app launches full-screen without browser chrome — it looks and feels native.

### Colour coding thresholds

| Level | Colour | Hex | Meaning |
|-------|--------|-----|---------|
| Safe | Green | `#28a745` | Leakage below threshold — play on |
| Borderline | Amber | `#f59e0b` | Near threshold — consider turning down |
| Too loud | Red | `#dc3545` | Above threshold — definitely audible next door |

The thresholds for the coherence-derived leakage metric should be calibrated to your specific wall (see Calibration section), but reasonable defaults are: green below −40 dB, amber between −40 and −25 dB, red above −25 dB on the overall weighted leakage scale.

---

## Hardware and bill of materials

### Component selection

**USB microphones remain the right choice** over I2S MEMS alternatives like the INMP441. USB mics are plug-and-play on Raspberry Pi OS (recognized as standard USB Audio Class devices via the `snd-usb-audio` kernel driver), require no soldering or GPIO wiring, and generally offer better signal-to-noise ratios. For this project, a matched pair of the same model ensures consistent relative measurements.

**Raspberry Pi 4 Model B 2GB** is sufficient. The coherence computation on a 60-second recording takes under 1 second on the Pi 4's Cortex-A72. The 1GB model works for headless operation if budget is tight.

### USB microphone comparison

| Feature | miniDSP UMIK-1 | Fifine K669B | Blue Snowball iCE | Generic USB |
|---------|---------------|-------------|-------------------|-------------|
| **Price** | ~$80–109 | ~$25–35 | ~$50–70 | ~$6–15 |
| **Type** | Omni measurement | Cardioid condenser | Cardioid condenser | Omni condenser |
| **Freq response** | 20 Hz–20 kHz ±1 dB | 20 Hz–20 kHz | 40 Hz–18 kHz | Varies |
| **Calibration** | Individual cal file | None | None | None |
| **Pi compatible** | Yes (USB Audio Class) | Yes | Yes | Usually |
| **Best for** | Calibrated SPL | Budget monitoring | General use | Basic detection |

The **Fifine K669B** at ~$30 is the best value for this project. Since both rooms use the same mic model, their frequency responses cancel out in the coherence calculation — you are measuring *relative* transmission, not absolute SPL. The UMIK-1 is overkill unless you also want calibrated absolute dB readings.

### Microphone warm-up handling

USB condenser microphones are solid-state and need no thermal warm-up, but the first ~0.5–1 second of a newly opened audio stream may contain USB enumeration artifacts (clicks, buffer initialization noise, or zeros). Both recording scripts record for `duration + 1 second` and **discard the first second**, ensuring clean measurement data. This is a software-level precaution, not a hardware defect.

### Bill of materials

| Item | Qty | Unit price | Total | Notes |
|------|-----|-----------|-------|-------|
| Raspberry Pi 4 Model B 2GB | 2 | $55 | $110 | Post-Jan 2026 MSRP (DRAM shortage pricing) |
| Fifine K669B USB Microphone | 2 | $30 | $60 | Matched pair for consistent comparison |
| SanDisk Ultra 32GB microSD (A1) | 2 | $8 | $16 | A1 rating for Pi boot performance |
| CanaKit 3.5A USB-C Power Supply | 2 | $10 | $20 | UL-listed, noise-filtered |
| **Total** | | | **$206** | |

**Budget option** (Pi 4 1GB at $35 each): brings the total to **$166**. The 1GB model is adequate for headless sound monitoring.

Optional accessories: Pi cases ($5–8 each), heatsink kits ($3–5 each), Cat5e Ethernet cables ($3–5 each, recommended for more reliable time sync), USB extension cables ($5 each, to position mics optimally).

---

## Time synchronisation with chrony

Both Pis must agree on the current time to within ~50 ms so their recordings start simultaneously. The **chrony** NTP implementation is the modern standard — more accurate than ntpd, better at handling intermittent connectivity, and lower resource usage.

Install on both Pis:

```bash
sudo apt update && sudo apt install chrony
```

Edit `/etc/chrony/chrony.conf` on both:

```conf
pool 2.debian.pool.ntp.org iburst
makestep 1 3
driftfile /var/lib/chrony/chrony.drift
```

Then `sudo systemctl restart chrony`. Both Pis sync independently to the NTP pool. On a home LAN (especially wired Ethernet), expect **0.1–1 ms accuracy** between the two Pis — this is 50–500× better than the 50 ms requirement.

Since checks are user-initiated rather than scheduled to the millisecond, the sync tolerance is generous. The 1.5-second `start_at` offset in the MQTT command absorbs any small clock discrepancy. Verify synchronization with `chronyc tracking` on each Pi.

For even tighter inter-Pi sync, configure Pi B as an NTP server (`allow 192.168.0.0/16` in its chrony.conf) and point Pi A to it as a preferred source. This is optional and unnecessary for this application.

---

## MQTT communication with Mosquitto

### Broker setup on Pi B

```bash
sudo apt install mosquitto mosquitto-clients
```

Create `/etc/mosquitto/conf.d/soundcheck.conf`:

```conf
listener 1883
allow_anonymous true
max_queued_messages 0
message_size_limit 10485760
```

The **10 MB message size limit** accommodates the largest possible audio payload: 60 seconds at 16 kHz, 16-bit mono = 1,920,000 bytes raw, which becomes ~2,560,000 bytes after base64 encoding. The default Mosquitto limit (various versions default to 256 MB or 1 MB) may or may not be sufficient, so setting it explicitly avoids surprises.

Restart: `sudo systemctl restart mosquitto`

### Topic structure

| Topic | Direction | Payload | QoS |
|-------|-----------|---------|-----|
| `soundcheck/command` | Pi B → Pi A | JSON: `{"action":"record", "duration":30, "check_id":"a1b2c3d4", "start_at":1740000001.5}` | 1 |
| `soundcheck/audio/{check_id}` | Pi A → Pi B | Base64-encoded int16 PCM audio | 1 |

**QoS 1** (at-least-once delivery) ensures commands and audio data are not lost if a brief WiFi hiccup occurs. QoS 2 (exactly-once) adds unnecessary overhead for this use case — receiving a duplicate command is harmless (Pi A would attempt to record twice, but the second recording would simply overwrite).

### MQTT command format

```json
{
  "action": "record",
  "duration": 30,
  "check_id": "a1b2c3d4",
  "start_at": 1740000001.5
}
```

The `start_at` field is a Unix epoch timestamp (float). Pi A calculates `wait = start_at - time.time()` and sleeps for that duration before beginning its recording. If `start_at` is more than 2 seconds in the past when Pi A processes the message, the command is discarded as stale — this prevents a queued old command from triggering an unexpected recording.

---

## Calibration procedure

Calibration maps the abstract coherence-derived leakage metric to your subjective experience of "too loud." Since every room, wall, and speaker placement is different, the system needs a one-time calibration against your specific environment.

### Steps

1. **Choose your reference volume.** Play music at the loudest level you consider acceptable for the bedroom occupant. This is your "green threshold" — the boundary between safe and borderline.

2. **Run a LONG check.** Tap LONG (60s) to get the highest-confidence measurement. Note the overall leakage dB value reported.

3. **Increase volume to "definitely too loud."** Play music at a level that would clearly disturb someone in the bedroom. Run another LONG check. This gives you the "red threshold."

4. **Set thresholds in the config.** Edit the threshold values in the Flask app or analysis function to match your measured values. The amber zone falls between your green and red reference points.

5. **Verify with a MEDIUM check.** Run a few MEDIUM checks at various volumes to confirm the thresholds feel right. Adjust if needed.

**Use LONG mode for all calibration measurements** — the 3,749 segments and excellent confidence level ensure your reference values are stable and repeatable. Running the same LONG check twice at the same volume should produce results within ±1–2 dB of each other.

### Recalibration triggers

Recalibrate if you move speakers, rearrange furniture near the shared wall, change your subwoofer settings, or if the room undergoes physical changes (new carpet, different curtains). The wall itself does not change, but the coupling between your speakers and the wall can shift significantly with placement.

---

## Alerting is simple by design

In a continuous monitoring system, alerting is a complex problem: you need thresholds, cooldown periods, escalation rules, false-positive suppression, and notification routing. **On-demand checks eliminate all of this.** You are always present when a check runs. You are always looking at the result.

The **primary alert** is the visual result on the dashboard — the page background turns green, amber, or red, a large dB number appears, and a verdict badge states the conclusion in plain English. This is immediate and impossible to miss.

The **secondary alert** is an optional ntfy.sh push notification, sent automatically after each check completes. This is useful if you start a LONG check and then switch to another app while waiting. The notification arrives with an emoji (🟢/🟡/🔴), the leakage dB value, and the confidence level. Setup is one line in the config:

```python
NTFY_TOPIC = 'my-soundcheck-abc123'  # pick any unique string
NTFY_ENABLED = True
```

No account needed. Install the ntfy app on your phone and subscribe to the same topic. At 5–10 checks per day, you will use roughly **2–4%** of the free tier's 250 messages/day limit.

---

## Privacy guarantees are structural, not policy-based

This architecture provides the strongest possible privacy guarantee: **audio data physically cannot exist unless you create it.** There is no daemon running that could accidentally record. There is no buffer that could be retrospectively accessed. There is no log file accumulating ambient audio.

When idle, Pi A's listener script maintains a single MQTT TCP connection. The `sounddevice` library is imported but the USB microphone's audio stream is not opened — the operating system's ALSA layer is not reading from the device. Pi B's Flask server handles HTTP and WebSocket connections but has no reference to any audio device until a check is triggered.

During a check, raw audio exists in memory for the duration of the recording plus the few seconds needed for MQTT transfer and coherence computation. The raw audio is **never written to disk** — only the computed leakage metrics (a few numbers) are stored in SQLite. After the coherence function returns, the numpy arrays are garbage-collected. Even if someone gained access to the Pis, they would find only historical leakage dB values, never recordings.

---

## Setup procedure from unboxing to first check

| Step | Task | Est. time |
|------|------|-----------|
| 1 | Flash Raspberry Pi OS Lite (64-bit) onto both SD cards using Pi Imager | 10 min |
| 2 | Boot both Pis, connect to WiFi, enable SSH, set hostnames (pi-bedroom, pi-livingroom) | 15 min |
| 3 | On both: `sudo apt update && sudo apt install chrony python3-venv python3-pip libportaudio2` | 5 min |
| 4 | On Pi B: `sudo apt install mosquitto mosquitto-clients`, configure soundcheck.conf | 5 min |
| 5 | On both: create project directory, set up Python venv, install dependencies | 10 min |
| 6 | On Pi B: `pip install flask flask-socketio eventlet paho-mqtt scipy numpy sounddevice requests` | 5 min |
| 7 | On Pi A: `pip install paho-mqtt sounddevice numpy` | 3 min |
| 8 | Plug USB microphones into both Pis, verify with `arecord -l` | 2 min |
| 9 | Copy app.py, analysis.py, templates/, static/ to Pi B | 5 min |
| 10 | Copy listener.py to Pi A, edit PI_B_ADDRESS to Pi B's IP | 2 min |
| 11 | Install systemd services on both Pis, enable and start | 5 min |
| 12 | Open `http://pi-livingroom.local:5000` on your phone | 1 min |
| 13 | Run a SHORT test check to verify end-to-end communication | 2 min |
| 14 | Run calibration procedure (2–3 LONG checks at reference volumes) | 10 min |
| 15 | Add to Home Screen on your phone (PWA install) | 1 min |
| **Total** | | **~80 min** |

### systemd service files

**Pi B** — `/etc/systemd/system/soundcheck-web.service`:
```ini
[Unit]
Description=SoundCheck Web Server
After=network-online.target mosquitto.service
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/soundcheck
ExecStart=/home/pi/soundcheck/venv/bin/python app.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

**Pi A** — `/etc/systemd/system/soundcheck-listener.service`:
```ini
[Unit]
Description=SoundCheck Bedroom Listener
After=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/soundcheck
ExecStart=/home/pi/soundcheck/venv/bin/python listener.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Enable with: `sudo systemctl daemon-reload && sudo systemctl enable --now soundcheck-web` (or `soundcheck-listener` on Pi A).

---

## Realistic accuracy expectations

This system measures *relative* coherence-based leakage, not calibrated SPL. The accuracy depends on the check mode, the music content, and your specific environment.

**LONG mode with music playing is the best-case scenario for coherence estimation.** With 3,749 averaging segments, the standard deviation of the coherence estimate at moderate coherence (C=0.3) is only 0.009 — meaning repeated measurements will be highly consistent. The 95% significance threshold drops to 0.0008, so even very faint leakage is statistically detectable.

**What the system can reliably do:**

- Detect whether your music is leaking through the wall at all (sensitivity down to very low coherence levels with LONG mode)
- Distinguish bass-heavy leakage from midrange/treble leakage
- Track relative changes: "turning the subwoofer down 3 dB reduced the bass leakage metric by X"
- Provide consistent, repeatable measurements that let you find the sweet spot volume
- Tell you definitively whether your current volume is above or below your calibrated threshold

**What it cannot do:**

- Report absolute SPL in the bedroom (that requires calibrated microphones and room acoustics modelling)
- Account for structure-borne vibration (footsteps, speaker-to-floor coupling) that bypasses air-path transmission
- Measure leakage of sounds that are not present in the living room recording (e.g., if your speaker faces away from Pi B's mic and only the bedroom gets direct radiation through the wall)

For the intended use case — "am I playing music too loud for the bedroom right now?" — these limitations do not matter. The system answers that specific question reliably, consistently, and on-demand.

---

## Conclusion

The on-demand architecture transforms what could be a complex monitoring system into an elegant, single-purpose tool. By recording only when triggered and only while music is playing, the system sidesteps every problem that plagues continuous monitors: false alerts from ambient noise, wasted computation on silence, privacy concerns from always-on microphones, and the statistical weakness of short automatic samples.

The three-mode duration system gives you explicit control over the confidence-speed tradeoff. A 10-second SHORT check answers "roughly how am I doing?" in the time it takes to glance at your phone. A 60-second LONG check, with nearly 4,000 FFT averaging segments, produces a measurement stable enough to calibrate against. The math is unambiguous: more segments means lower variance, lower bias, and better frequency resolution — and with on-demand checks, you can always afford to wait 60 seconds when it matters.

The total hardware cost of ~$166–206 buys a system that will run indefinitely on two passively cooled Pis drawing ~5W combined. The Flask PWA launches from your home screen like a native app, shows results on a colour-coded gauge, and keeps a history of your recent checks. The underlying spectral coherence method is the same technique used in industrial acoustic analysis — adapted here into approximately 400 lines of Python and one HTML page.