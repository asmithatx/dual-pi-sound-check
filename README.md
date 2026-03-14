# SoundCheck 🔊

**On-demand bedroom sound leakage monitor using dual Raspberry Pi 4 and spectral coherence analysis.**

Tap a button on your phone → both Pis record simultaneously → the system measures how much of your bedroom music is actually reaching the downstairs living room, filtering out everything else (TV, conversation, HVAC).

No continuous recording. No background listening. Audio exists in RAM only during an active check, then is immediately discarded.

---

## How it works

Two Raspberry Pi 4 devices sit in separate rooms, each with a USB microphone:

- **Pi B** (living room) — runs the Flask web server, MQTT broker, and coherence analysis
- **Pi A** (bedroom) — runs a lightweight MQTT listener, idle until triggered

When you tap **SHORT / MEDIUM / LONG** on the mobile dashboard, both Pis record simultaneously. Pi B uses `scipy.signal.coherence` (Welch's method) to compute the magnitude-squared coherence between the two recordings at every frequency. Since your music is present in *both* rooms but the TV/conversation in the living room is *not* in the bedroom, coherence separates them automatically. The result is a leakage estimate weighted toward bass frequencies (which pass through walls most easily).

### Check modes

| Mode | Duration | FFT segments | Confidence |
|------|----------|-------------|------------|
| SHORT | 10 s | ~600 | Medium |
| MEDIUM | 30 s | ~1,900 | High |
| LONG | 60 s | ~3,700 | Excellent |

More segments = lower variance = more reliable estimate. Use LONG for calibration and whenever you want a definitive answer.

---

## Hardware

| Item | Qty | ~Cost |
|------|-----|-------|
| Raspberry Pi 4 Model B (2 GB) | 2 | $110 |
| USB microphone (e.g. Fifine K669B) | 2 | $60 |
| SanDisk Ultra 32 GB microSD (A1) | 2 | $16 |
| USB-C power supply (3.5 A) | 2 | $20 |
| **Total** | | **~$206** |

A matched pair of the same USB microphone model is strongly recommended — any unit-to-unit sensitivity difference is corrected during calibration.

---

## Prerequisites

Both Pis need:
- **Raspberry Pi OS Lite (64-bit)**, Bookworm or later
- Python 3.11+
- `libportaudio2` (for `sounddevice`)

Pi B additionally needs:
- Mosquitto MQTT broker

---

## Installation

### 1. Clone on both Pis

```bash
git clone https://github.com/YOUR_USERNAME/soundcheck.git
cd soundcheck
```

### 2. System packages

```bash
# Both Pis:
sudo apt update
sudo apt install -y python3-venv python3-pip libportaudio2 chrony

# Pi B only:
sudo apt install -y mosquitto mosquitto-clients
```

### 3. Python environment

**Pi B:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-pib.txt
```

**Pi A:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-pia.txt
```

### 4. Configure

Edit `config.yaml` on **both Pis**:

```yaml
mqtt:
  broker_host: "192.168.1.XXX"   # ← Pi B's actual IP address

ntfy:
  topic: "my-soundcheck-abc123"  # ← pick a unique private string
```

Find Pi B's IP with `hostname -I`.

### 5. Mosquitto (Pi B only)

```bash
sudo cp mosquitto/soundcheck.conf /etc/mosquitto/conf.d/
sudo systemctl restart mosquitto
sudo systemctl enable mosquitto
```

### 6. Generate PWA icons

```bash
# On Pi B:
python generate_icons.py
# Optionally: pip install Pillow first for proper icons
```

### 7. Time sync (both Pis)

```bash
sudo systemctl enable chrony --now
chronyc tracking   # verify sync — should show offset < 10 ms
```

### 8. Verify microphone

```bash
# On each Pi, plug in USB mic and check it appears:
arecord -l
# Should list something like: card 1: Device [USB Audio Device]

# Quick 3-second test recording:
arecord -D hw:1,0 -d 3 -f S16_LE -r 16000 test.wav && aplay test.wav
```

### 9. Install systemd services

**Pi B:**
```bash
sudo cp systemd/soundcheck-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now soundcheck-web
sudo systemctl status soundcheck-web
```

**Pi A:**
```bash
sudo cp systemd/soundcheck-listener.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now soundcheck-listener
sudo systemctl status soundcheck-listener
```

### 10. First check

Open `http://<pi-b-ip>:5000` on your phone. Tap **SHORT** to verify end-to-end communication. You should see a result within ~15 seconds.

---

## Calibration (important — do this before relying on results)

With both Pis running and microphones in place:

```bash
# On Pi B:
source venv/bin/activate
python calibration.py
```

Follow the on-screen prompts:
1. **Mic matching** — place both mics side-by-side, play noise, measure sensitivity offset
2. **Quiet baseline** — rooms silent, measure noise floor
3. **Green threshold** — play music at loudest acceptable level, run 2× LONG checks
4. **Red threshold** — play music at clearly-too-loud level, run 2× LONG checks

The script writes thresholds back to `config.yaml` and restarts are not required — thresholds are read at startup.

---

## Dashboard (mobile PWA)

Open `http://<pi-b-ip>:5000` on your phone. To install as a home screen app:

- **Android (Chrome):** tap ⋮ → *Add to Home screen*
- **iOS (Safari):** tap Share → *Add to Home Screen*

The app launches full-screen without browser chrome.

### Reading the results

| Colour | Meaning |
|--------|---------|
| 🟢 Green | Leakage below your calibrated safe threshold |
| 🟡 Amber | Approaching threshold — consider turning down |
| 🔴 Red | Above threshold — definitely audible downstairs |

The **band breakdown** (Bass / Mid / Treble) shows where the leakage is coming from. Bass leakage near 0 dB is expected physics — walls don't stop low frequencies well.

---

## Push notifications (optional)

Results are also sent to [ntfy.sh](https://ntfy.sh) after each check:

1. Install the **ntfy** app on your phone (Android/iOS)
2. Subscribe to your topic (the string you set in `config.yaml`)
3. Done — you'll receive a push notification with 🟢/🟡/🔴 and the dB value

Free tier: 250 messages/day. At 5–10 checks/day, this is plenty.

---

## Project structure

```
soundcheck/
├── app.py                  # Pi B: Flask web server + orchestrator
├── listener.py             # Pi A: MQTT listener + recorder
├── analysis.py             # Spectral coherence engine (shared)
├── calibration.py          # One-time calibration wizard (run on Pi B)
├── generate_icons.py       # PWA icon generator (run once)
├── config.yaml             # All configuration (edit before first run)
├── requirements-pib.txt    # Pi B Python dependencies
├── requirements-pia.txt    # Pi A Python dependencies
├── templates/
│   └── index.html          # Mobile PWA dashboard
├── static/
│   ├── css/style.css       # Custom dark-theme styles
│   ├── js/dashboard.js     # Socket.IO client, gauge, history
│   ├── manifest.json       # PWA manifest
│   ├── sw.js               # Service worker (app shell cache)
│   └── icons/              # PWA icons (generated by generate_icons.py)
├── systemd/
│   ├── soundcheck-web.service      # Pi B systemd unit
│   └── soundcheck-listener.service # Pi A systemd unit
└── mosquitto/
    └── soundcheck.conf     # Mosquitto configuration
```

---

## Troubleshooting

**"Timed out waiting for bedroom audio"**
- Is Pi A's listener service running? `sudo systemctl status soundcheck-listener`
- Can Pi B reach Pi A over MQTT? `mosquitto_sub -h localhost -t 'soundcheck/#' -v`
- Check Pi A's logs: `journalctl -u soundcheck-listener -f`

**No microphone detected**
- `arecord -l` — does the USB mic appear?
- If multiple audio devices exist, you may need to set `hw:1,0` explicitly in `sounddevice.default.device`

**Results seem wrong / all green or all red**
- Run calibration to set proper thresholds for your specific rooms
- Ensure the living room is quiet (no TV, no conversation) during checks

**Coherence confidence shows "low"**
- Use MEDIUM or LONG mode — short clips produce fewer FFT segments
- Make sure music is playing at a noticeable volume during the check

**App not loading on phone**
- Ensure phone and both Pis are on the same WiFi network
- Try `http://<ip>:5000` directly (mDNS may not resolve on all phones)

---

## Accuracy expectations

This system measures **relative leakage**, not calibrated absolute SPL. Expected accuracy after calibration: **±3–5 dB** (MEDIUM/LONG modes).

The method works best with:
- Broadband music (bass + midrange + treble simultaneously)
- Sustained audio over the full check duration
- A quiet living room (no competing local sounds)

Structure-borne vibration (speaker-to-floor coupling) is not measured — if your subwoofer is sitting on the floor directly above the living room ceiling, that path bypasses this system entirely.

---

## Licence

MIT — see [LICENSE](LICENSE)
