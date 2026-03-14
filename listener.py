"""
listener.py — SoundCheck bedroom listener (Pi A)

This is the *only* script that runs on the bedroom Pi.
It idles at near-zero CPU, listening on MQTT for record commands.
No audio is captured until an explicit command arrives.

Usage:
    python listener.py                      # uses config.yaml in same dir
    python listener.py --config /path/to/config.yaml
"""

import argparse
import base64
import json
import logging
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import paho.mqtt.client as mqtt
import yaml

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("soundcheck.listener")

# ── CLI ───────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="SoundCheck bedroom listener")
parser.add_argument(
    "--config",
    default=str(Path(__file__).parent / "config.yaml"),
    help="Path to config.yaml",
)
args = parser.parse_args()

# ── Config ────────────────────────────────────────────────────────────────────
with open(args.config) as f:
    cfg = yaml.safe_load(f)

PI_B_HOST    = cfg["mqtt"]["broker_host"]
MQTT_PORT    = cfg["mqtt"]["broker_port"]
SAMPLE_RATE  = cfg["audio"]["sample_rate"]
CHANNELS     = cfg["audio"]["channels"]
WARMUP_SEC   = cfg["audio"]["warmup_seconds"]
MAX_DURATION = cfg["audio"].get("max_duration_seconds", 65)


# ── Audio ─────────────────────────────────────────────────────────────────────

def record_and_send(client: mqtt.Client, duration: int, check_id: str) -> None:
    """
    Record audio, trim warm-up, encode as base64, publish via MQTT.
    Total recording = duration + WARMUP_SEC; first WARMUP_SEC seconds discarded.
    """
    total_sec  = duration + WARMUP_SEC
    total_samp = int(total_sec * SAMPLE_RATE)

    log.info(
        f"[{check_id}] Recording {total_sec}s "
        f"(discarding first {WARMUP_SEC}s warm-up)"
    )

    try:
        buf = sd.rec(total_samp, samplerate=SAMPLE_RATE,
                     channels=CHANNELS, dtype="int16")
        sd.wait()
    except Exception as exc:
        log.error(f"[{check_id}] Recording failed: {exc}")
        return

    # Drop warm-up period
    trimmed     = buf[WARMUP_SEC * SAMPLE_RATE :]
    raw_bytes   = trimmed.tobytes()
    b64_payload = base64.b64encode(raw_bytes)

    topic = f"soundcheck/audio/{check_id}"
    info  = client.publish(topic, b64_payload, qos=1)
    info.wait_for_publish(timeout=10)

    log.info(
        f"[{check_id}] Sent {len(b64_payload):,} bytes → {topic} "
        f"(mid={info.mid})"
    )


# ── MQTT callbacks ────────────────────────────────────────────────────────────

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        client.subscribe("soundcheck/command", qos=1)
        log.info(f"Connected to MQTT broker at {PI_B_HOST}:{MQTT_PORT}")
    else:
        log.error(f"MQTT connection refused (rc={rc}). Will retry…")


def on_disconnect(client, userdata, rc):
    if rc != 0:
        log.warning(f"Unexpected MQTT disconnect (rc={rc}). Reconnecting…")


def on_message(client, userdata, msg):
    try:
        cmd = json.loads(msg.payload.decode())
    except json.JSONDecodeError as exc:
        log.warning(f"Invalid JSON on {msg.topic}: {exc}")
        return

    if cmd.get("action") != "record":
        return

    duration  = int(cmd.get("duration", 30))
    check_id  = cmd.get("check_id", "unknown")
    start_at  = float(cmd.get("start_at", time.time()))

    if duration > MAX_DURATION:
        log.warning(
            f"[{check_id}] Requested duration {duration}s exceeds "
            f"max {MAX_DURATION}s — clamping"
        )
        duration = MAX_DURATION

    # ── Wait until synchronised start time ───────────────────────────────
    wait = start_at - time.time()
    if wait < -5:
        log.warning(
            f"[{check_id}] start_at is {-wait:.1f}s in the past — "
            "command too stale, skipping"
        )
        return
    if wait > 0:
        log.info(f"[{check_id}] Waiting {wait:.3f}s until start_at…")
        time.sleep(max(wait, 0))

    record_and_send(client, duration, check_id)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    client = mqtt.Client(client_id="soundcheck-pia")
    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.on_message    = on_message

    # Reconnect automatically
    client.reconnect_delay_set(min_delay=2, max_delay=30)

    log.info(f"Connecting to MQTT broker at {PI_B_HOST}:{MQTT_PORT}…")
    client.connect(PI_B_HOST, MQTT_PORT, keepalive=60)

    log.info("Bedroom listener ready — idle, waiting for commands")
    # loop_forever() blocks on an epoll socket → ~0 CPU when idle
    client.loop_forever()


if __name__ == "__main__":
    main()
