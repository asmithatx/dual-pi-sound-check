"""
calibration.py — SoundCheck calibration utility
Runs on Pi B. Guides you through a one-time calibration session and
writes the measured thresholds back to config.yaml.

Usage:
    python calibration.py
"""

import json
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import paho.mqtt.client as mqtt
import yaml

from analysis import analyze_bedroom_leakage, MODE_CONFIG

CONFIG_PATH = Path(__file__).parent / "config.yaml"

with open(CONFIG_PATH) as f:
    cfg = yaml.safe_load(f)

SAMPLE_RATE = cfg["audio"]["sample_rate"]
CHANNELS    = cfg["audio"]["channels"]
WARMUP_SEC  = cfg["audio"]["warmup_seconds"]
MQTT_HOST   = cfg["mqtt"]["broker_host"]
MQTT_PORT   = cfg["mqtt"]["broker_port"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def print_header(text: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}")


def wait_for_enter(prompt: str = "Press ENTER when ready…") -> None:
    input(f"\n>>> {prompt}")


def record_both(duration: int, label: str) -> tuple:
    """
    Trigger both Pis to record simultaneously.
    Returns (bedroom_audio, livingroom_audio) as float64 arrays.
    """
    import base64
    from threading import Event

    received_audio = {}
    audio_event    = Event()
    check_id       = f"cal_{int(time.time())}"

    def _on_connect(client, userdata, flags, rc):
        if rc == 0:
            client.subscribe(f"soundcheck/audio/{check_id}", qos=1)

    def _on_message(client, userdata, msg):
        raw   = base64.b64decode(msg.payload)
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
        received_audio["bedroom"] = audio
        audio_event.set()

    client = mqtt.Client(client_id="soundcheck-calibration")
    client.on_connect = _on_connect
    client.on_message = _on_message
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_start()
    time.sleep(0.2)

    start_at = time.time() + 1.5
    command  = json.dumps({
        "action":   "record",
        "duration": duration,
        "check_id": check_id,
        "start_at": start_at,
    })
    client.publish("soundcheck/command", command, qos=1)

    # Record locally
    wait = start_at - time.time()
    if wait > 0:
        time.sleep(wait)

    print(f"  Recording {label} ({duration}s)…", end="", flush=True)
    total  = duration + WARMUP_SEC
    buf    = sd.rec(int(total * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                    channels=CHANNELS, dtype="int16")
    sd.wait()
    local  = buf[WARMUP_SEC * SAMPLE_RATE:, 0].astype(np.float64) / 32768.0
    print(" done.")

    # Wait for bedroom audio
    print("  Waiting for bedroom audio…", end="", flush=True)
    got = audio_event.wait(timeout=15)
    client.loop_stop()
    client.disconnect()

    if not got:
        print(" TIMEOUT!")
        print("  ⚠  Could not receive audio from Pi A. Is it running?")
        sys.exit(1)

    print(" received.")
    return received_audio["bedroom"], local


def analyse(bedroom, living, label: str) -> dict:
    result = analyze_bedroom_leakage(
        bedroom_audio    = bedroom,
        livingroom_audio = living,
        sample_rate      = SAMPLE_RATE,
        mode             = "long",
    )
    print(
        f"  Result: {result['overall_leakage_db']:+.1f} dB  "
        f"(bass {result['band_breakdown']['bass']['leakage_db']:+.1f}  "
        f"mid {result['band_breakdown']['midrange']['leakage_db']:+.1f}  "
        f"treble {result['band_breakdown']['treble']['leakage_db']:+.1f})  "
        f"[{result['confidence']} confidence]"
    )
    return result


# ── Calibration steps ─────────────────────────────────────────────────────────

def step_mic_matching() -> float:
    """Place both mics in the same room, measure sensitivity offset."""
    print_header("Step 1: Microphone matching")
    print(
        "  Place BOTH microphones in the same room, about 30 cm apart.\n"
        "  Play pink noise or music from a nearby speaker.\n"
        "  This measures any sensitivity difference between the two mics."
    )
    wait_for_enter()

    durations = [30, 30]
    offsets   = []
    for i, dur in enumerate(durations):
        bedroom, local = record_both(dur, f"mic-match run {i+1}")
        br_rms = np.sqrt(np.mean(bedroom**2))
        lr_rms = np.sqrt(np.mean(local**2))
        offset = 20 * np.log10(max(br_rms, 1e-10)) - 20 * np.log10(max(lr_rms, 1e-10))
        offsets.append(offset)
        print(f"  Mic offset (run {i+1}): {offset:+.2f} dB")

    mean_offset = float(np.mean(offsets))
    print(f"\n  ✓ Mic sensitivity offset: {mean_offset:+.2f} dB (bedroom vs living room)")
    print(  "    (stored in config — applied automatically during analysis)")
    return mean_offset


def step_quiet_baseline() -> dict:
    """Measure noise floor in both rooms."""
    print_header("Step 2: Quiet baseline")
    print(
        "  Move both mics to their permanent positions:\n"
        "    Pi A mic → bedroom\n"
        "    Pi B mic → living room\n"
        "  Ensure BOTH rooms are as quiet as possible (no music, TV off)."
    )
    wait_for_enter()

    bedroom, local  = record_both(30, "quiet baseline")
    br_rms = float(20 * np.log10(np.sqrt(np.mean(bedroom**2)) + 1e-10))
    lr_rms = float(20 * np.log10(np.sqrt(np.mean(local**2))   + 1e-10))
    print(f"  Bedroom noise floor:     {br_rms:+.1f} dBFS")
    print(f"  Living room noise floor: {lr_rms:+.1f} dBFS")
    return {"bedroom_db": round(br_rms, 1), "livingroom_db": round(lr_rms, 1)}


def step_reference_level(label: str, instructions: str) -> float:
    """Measure leakage at a user-defined reference volume."""
    print_header(f"Step 3{label}: Reference level — {instructions}")
    print("  Uses LONG mode (60s) for highest accuracy calibration.")
    wait_for_enter()

    readings = []
    for run in range(1, 3):
        print(f"  Run {run}/2:")
        bedroom, local = record_both(60, f"{instructions} run {run}")
        result         = analyse(bedroom, local, instructions)
        readings.append(result["overall_leakage_db"])

    mean_db = float(np.mean(readings))
    print(f"\n  ✓ {instructions}: mean = {mean_db:+.1f} dB")
    return round(mean_db, 1)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*60)
    print("  SoundCheck Calibration Wizard")
    print("  This takes about 10–15 minutes.")
    print("  Prerequisite: both Pis are running and mics are plugged in.")
    print("="*60)

    print("\nStep overview:")
    print("  1. Microphone matching  (both mics in same room)")
    print("  2. Quiet baseline       (mics in place, rooms silent)")
    print("  3. Green threshold      (loudest acceptable volume)")
    print("  4. Red threshold        (clearly too loud volume)")
    wait_for_enter("Press ENTER to begin calibration…")

    mic_offset = step_mic_matching()
    baseline   = step_quiet_baseline()

    print_header("Step 3: Green threshold")
    print(
        "  Play music in the bedroom at the LOUDEST level you consider\n"
        "  acceptable downstairs. This sets your 'safe' limit.\n"
        "  The living room should be quiet (no TV, no conversation)."
    )
    green_db = step_reference_level("a", "loudest acceptable volume")

    print_header("Step 4: Red threshold")
    print(
        "  Now play music at a level that is CLEARLY TOO LOUD downstairs.\n"
        "  The living room should still be quiet."
    )
    red_db = step_reference_level("b", "clearly too loud")

    # amber sits midway between green and red
    amber_db = round((green_db + red_db) / 2, 1)

    print_header("Calibration complete — results")
    print(f"  Mic offset:         {mic_offset:+.2f} dB")
    print(f"  Bedroom noise floor:{baseline['bedroom_db']:+.1f} dBFS")
    print(f"  LR noise floor:     {baseline['livingroom_db']:+.1f} dBFS")
    print(f"  Green threshold:    ≤ {green_db:+.1f} dB  (safe)")
    print(f"  Amber threshold:    ≤ {amber_db:+.1f} dB  (borderline)")
    print(f"  Red threshold:      > {red_db:+.1f} dB  (too loud)")

    # Write back to config.yaml
    cfg["thresholds"]["red_db"]   = red_db
    cfg["thresholds"]["amber_db"] = green_db   # green boundary = amber floor
    cfg["calibration"] = {
        "mic_offset_db":          round(mic_offset, 2),
        "noise_floor_bedroom_db": baseline["bedroom_db"],
        "noise_floor_lr_db":      baseline["livingroom_db"],
        "last_calibrated":        time.strftime("%Y-%m-%d"),
    }

    with open(CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)

    print(f"\n  ✓ config.yaml updated. Restart app.py to apply new thresholds.")


if __name__ == "__main__":
    main()
