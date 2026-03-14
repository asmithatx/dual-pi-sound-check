"""
app.py — SoundCheck web server (Pi B / Living Room)

Responsibilities:
  • Serves the mobile PWA dashboard
  • Orchestrates on-demand checks via MQTT
  • Records local audio during each check
  • Runs spectral coherence analysis
  • Stores results in SQLite
  • Emits real-time progress to the browser via WebSocket
  • Sends optional ntfy.sh push notifications
"""

import eventlet
eventlet.monkey_patch()          # must be first, before all other imports

import base64
import json
import logging
import sqlite3
import time
import uuid
from threading import Event
from pathlib import Path

import numpy as np
import sounddevice as sd
import requests
import yaml

from flask import Flask, jsonify, render_template, send_from_directory
from flask_socketio import SocketIO, emit
import paho.mqtt.client as mqtt

from analysis import analyze_bedroom_leakage, MODE_CONFIG

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("soundcheck.server")

# ── Config ────────────────────────────────────────────────────────────────────
CONFIG_PATH = Path(__file__).parent / "config.yaml"

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

cfg = load_config()

SAMPLE_RATE    = cfg["audio"]["sample_rate"]
CHANNELS       = cfg["audio"]["channels"]
WARMUP_SEC     = cfg["audio"]["warmup_seconds"]
MQTT_BROKER    = cfg["mqtt"]["broker_host"]
MQTT_PORT      = cfg["mqtt"]["broker_port"]
NTFY_TOPIC     = cfg["ntfy"]["topic"]
NTFY_ENABLED   = cfg["ntfy"]["enabled"]
DB_PATH        = Path(__file__).parent / cfg["database"]["path"]
HISTORY_LIMIT  = cfg["database"]["history_limit"]
THRESHOLDS     = cfg["thresholds"]

# ── Flask / SocketIO ──────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["SECRET_KEY"] = cfg.get("secret_key", "change-me-in-production")
socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*",
                    logger=False, engineio_logger=False)

# ── SQLite ────────────────────────────────────────────────────────────────────

def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS checks (
            id                TEXT PRIMARY KEY,
            timestamp         REAL NOT NULL,
            mode              TEXT NOT NULL,
            duration_seconds  INTEGER NOT NULL,
            overall_leakage_db REAL,
            bass_db           REAL,
            midrange_db       REAL,
            treble_db         REAL,
            confidence        TEXT,
            result_color      TEXT,
            n_segments        INTEGER
        )
    """)
    conn.commit()
    conn.close()
    log.info(f"Database ready at {DB_PATH}")


def save_result(check_id: str, mode: str, duration: int, result: dict) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO checks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            check_id,
            time.time(),
            mode,
            duration,
            result["overall_leakage_db"],
            result["band_breakdown"]["bass"]["leakage_db"],
            result["band_breakdown"]["midrange"]["leakage_db"],
            result["band_breakdown"]["treble"]["leakage_db"],
            result["confidence"],
            result["result_color"],
            result["n_segments"],
        ),
    )
    conn.commit()
    conn.close()


def get_history(limit: int = HISTORY_LIMIT) -> list:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM checks ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


init_db()

# ── MQTT ──────────────────────────────────────────────────────────────────────
# Stores audio received from Pi A, keyed by check_id
_remote_audio: dict  = {}
_audio_events: dict  = {}   # check_id → threading.Event


def _on_mqtt_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info("Connected to MQTT broker")
        client.subscribe("soundcheck/audio/#", qos=1)
    else:
        log.error(f"MQTT connection failed (rc={rc})")


def _on_mqtt_message(client, userdata, msg):
    if not msg.topic.startswith("soundcheck/audio/"):
        return
    check_id = msg.topic.split("/")[-1]
    try:
        raw   = base64.b64decode(msg.payload)
        audio = (
            np.frombuffer(raw, dtype=np.int16)
              .astype(np.float64) / 32768.0
        )
        _remote_audio[check_id] = audio
        if check_id in _audio_events:
            _audio_events[check_id].set()
        log.info(
            f"[{check_id}] Received bedroom audio: "
            f"{len(audio)} samples ({len(audio)/SAMPLE_RATE:.1f}s)"
        )
    except Exception as exc:
        log.error(f"[{check_id}] Failed to decode audio: {exc}")


mqtt_client = mqtt.Client(client_id="soundcheck-pib")
mqtt_client.on_connect = _on_mqtt_connect
mqtt_client.on_message = _on_mqtt_message

try:
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    mqtt_client.loop_start()
    log.info(f"MQTT client connecting to {MQTT_BROKER}:{MQTT_PORT}")
except Exception as exc:
    log.warning(f"MQTT connection deferred: {exc}")


# ── Audio recording ───────────────────────────────────────────────────────────

def record_local(duration_sec: int) -> np.ndarray:
    """
    Record from the local USB microphone.
    Records duration_sec + WARMUP_SEC seconds, discards the first WARMUP_SEC
    seconds to avoid USB enumeration artifacts, returns float64 array in [-1,1].
    """
    total = duration_sec + WARMUP_SEC
    log.info(f"Recording {total}s locally (first {WARMUP_SEC}s discarded)")
    buf = sd.rec(
        int(total * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="int16",
    )
    sd.wait()
    trimmed = buf[WARMUP_SEC * SAMPLE_RATE :, 0]
    return trimmed.astype(np.float64) / 32768.0


# ── Alerting ──────────────────────────────────────────────────────────────────

def send_ntfy(result: dict) -> None:
    if not NTFY_ENABLED or not NTFY_TOPIC:
        return
    icons = {"green": "🟢", "amber": "🟡", "red": "🔴"}
    icon  = icons.get(result["result_color"], "⚪")
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=(
                f"{icon} Leakage: {result['overall_leakage_db']:.1f} dB  "
                f"| {result['confidence']} confidence  "
                f"| {result['mode']} check"
            ),
            headers={
                "Title":    "SoundCheck Result",
                "Priority": "default" if result["result_color"] == "green" else "high",
                "Tags":     "speaker,sound_level",
            },
            timeout=5,
        )
        log.info("ntfy.sh notification sent")
    except Exception as exc:
        log.warning(f"ntfy.sh notification failed: {exc}")


# ── Flask routes ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", modes=MODE_CONFIG)


@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json",
                               mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    return send_from_directory("static", "sw.js",
                               mimetype="application/javascript")


@app.route("/api/history")
def api_history():
    return jsonify(get_history())


@app.route("/api/config")
def api_config():
    """Expose thresholds to the front-end for gauge colouring."""
    return jsonify(THRESHOLDS)


# ── SocketIO events ───────────────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    log.info("Browser client connected")


@socketio.on("start_check")
def handle_start_check(data):
    mode = data.get("mode", "medium").lower()
    if mode not in MODE_CONFIG:
        emit("check_error", {"message": f"Unknown mode: {mode}"})
        return

    duration   = MODE_CONFIG[mode]["duration"]
    check_id   = uuid.uuid4().hex[:8]
    start_at   = time.time() + 1.5          # 1.5s for MQTT delivery + prep

    _audio_events[check_id] = Event()

    command = json.dumps({
        "action":   "record",
        "duration": duration,
        "check_id": check_id,
        "start_at": start_at,
    })
    mqtt_client.publish("soundcheck/command", command, qos=1)
    log.info(
        f"[{check_id}] Check started: mode={mode}, duration={duration}s"
    )

    socketio.start_background_task(
        _run_check, check_id, mode, duration, start_at
    )


def _run_check(check_id: str, mode: str, duration: int, start_at: float):
    """Background task: record → wait for Pi A → analyse → report."""
    try:
        # ── Wait for synchronised start ───────────────────────────────────
        wait = start_at - time.time()
        if wait > 0:
            eventlet.sleep(wait)

        # ── Record locally with per-second progress updates ───────────────
        total_samples = int((duration + WARMUP_SEC) * SAMPLE_RATE)
        buf = sd.rec(total_samples, samplerate=SAMPLE_RATE,
                     channels=CHANNELS, dtype="int16")

        for elapsed in range(1, duration + 1):
            eventlet.sleep(1)
            socketio.emit("check_progress", {
                "phase":    "recording",
                "elapsed":  elapsed,
                "duration": duration,
                "progress": round(100 * elapsed / duration),
            })

        sd.wait()
        local_audio = (
            buf[WARMUP_SEC * SAMPLE_RATE :, 0]
              .astype(np.float64) / 32768.0
        )

        # ── Wait for Pi A's audio ─────────────────────────────────────────
        socketio.emit("check_progress", {"phase": "waiting_for_remote",
                                         "progress": 100})
        received = _audio_events[check_id].wait(timeout=20)

        if not received:
            del _audio_events[check_id]
            socketio.emit("check_error", {
                "message": "Timed out waiting for bedroom audio (20s). "
                           "Is Pi A online?"
            })
            return

        remote_audio = _remote_audio.pop(check_id, None)
        del _audio_events[check_id]

        if remote_audio is None:
            socketio.emit("check_error", {"message": "No audio data from bedroom Pi."})
            return

        # ── Coherence analysis ────────────────────────────────────────────
        socketio.emit("check_progress", {"phase": "analysing", "progress": 100})
        result = analyze_bedroom_leakage(
            bedroom_audio    = remote_audio,
            livingroom_audio = local_audio,
            sample_rate      = SAMPLE_RATE,
            mode             = mode,
        )

        # ── Persist ───────────────────────────────────────────────────────
        save_result(check_id, mode, duration, result)

        # ── Send to browser ───────────────────────────────────────────────
        socketio.emit("check_complete", {
            "check_id":     check_id,
            "mode":         mode,
            "leakage_db":   result["overall_leakage_db"],
            "confidence":   result["confidence"],
            "result_color": result["result_color"],
            "bass_db":      result["band_breakdown"]["bass"]["leakage_db"],
            "mid_db":       result["band_breakdown"]["midrange"]["leakage_db"],
            "treble_db":    result["band_breakdown"]["treble"]["leakage_db"],
            "n_segments":   result["n_segments"],
            "duration_sec": result["duration_sec"],
            "processing_ms": result["processing_ms"],
        })

        # ── Optional push notification ────────────────────────────────────
        send_ntfy(result)

        log.info(
            f"[{check_id}] Complete: {result['overall_leakage_db']} dB "
            f"({result['result_color']}, {result['confidence']})"
        )

    except Exception as exc:
        log.error(f"[{check_id}] Check failed: {exc}", exc_info=True)
        socketio.emit("check_error", {"message": str(exc)})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("SoundCheck server starting on http://0.0.0.0:5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
