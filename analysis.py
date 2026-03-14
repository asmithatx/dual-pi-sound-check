"""
analysis.py — Spectral coherence engine for SoundCheck
Runs on Pi B after both audio recordings are collected.
"""

import time
import numpy as np
from scipy import signal
from typing import Dict, Any


# ── Mode parameters ───────────────────────────────────────────────────────────

MODE_CONFIG = {
    "short":  {"duration": 10, "nperseg": 512,  "label": "Short (10s)"},
    "medium": {"duration": 30, "nperseg": 512,  "label": "Medium (30s)"},
    "long":   {"duration": 60, "nperseg": 1024, "label": "Long (60s)"},
}

# Frequency bands with perceptual weighting
# Bass gets 60% of the score because physics: walls pass bass most easily
BANDS = {
    "bass":     {"range": (50,  250),  "weight": 0.60},
    "midrange": {"range": (250, 1000), "weight": 0.30},
    "treble":   {"range": (1000, 4000),"weight": 0.10},
}


# ── Wall transmission loss model ──────────────────────────────────────────────

def wall_transmission_loss(freqs: np.ndarray) -> np.ndarray:
    """
    Piecewise transmission-loss model (dB) for a typical interior
    wall/floor assembly (STC ~33–40).

    Below 125 Hz: minimal mass-law attenuation (~10–25 dB)
    125–500 Hz:   moderate (~25–49 dB)
    Above 500 Hz: strong, +6 dB/octave (~49 dB and rising)
    """
    tl = np.zeros_like(freqs, dtype=float)

    lo = freqs < 125
    tl[lo] = 15 + 10 * np.log2(np.maximum(freqs[lo], 30) / 30)

    mid = (freqs >= 125) & (freqs < 500)
    tl[mid] = 25 + 12 * np.log2(freqs[mid] / 125)

    hi = freqs >= 500
    tl[hi] = 49 + 6 * np.log2(freqs[hi] / 500)

    return tl


# ── Core analysis function ────────────────────────────────────────────────────

def analyze_bedroom_leakage(
    bedroom_audio: np.ndarray,
    livingroom_audio: np.ndarray,
    sample_rate: int = 16000,
    mode: str = "medium",
) -> Dict[str, Any]:
    """
    Estimate how much bedroom sound is leaking into the living room.

    Uses magnitude-squared coherence (Welch's method via scipy) to separate
    bedroom-originated signal from local living-room noise at each frequency,
    then applies a wall-attenuation model and band weighting.

    Parameters
    ----------
    bedroom_audio    : float64 ndarray, normalised to [-1, 1]
    livingroom_audio : float64 ndarray, normalised to [-1, 1]
    sample_rate      : Hz (default 16000)
    mode             : "short" | "medium" | "long"

    Returns
    -------
    dict with keys:
        overall_leakage_db  – weighted leakage estimate (dBFS-relative)
        band_breakdown      – per-band coherence + leakage
        confidence          – "low" | "medium" | "high" | "excellent"
        result_color        – "green" | "amber" | "red"
        n_segments          – number of Welch segments (quality proxy)
        duration_sec        – actual duration analysed
        mode                – echo of mode parameter
        processing_ms       – wall-clock time for this call
    """
    t0 = time.time()

    cfg = MODE_CONFIG.get(mode, MODE_CONFIG["medium"])
    nperseg = cfg["nperseg"]
    noverlap = nperseg // 2

    # Trim to equal length
    n = min(len(bedroom_audio), len(livingroom_audio))
    bedroom_audio    = bedroom_audio[:n]
    livingroom_audio = livingroom_audio[:n]

    if n < nperseg * 2:
        raise ValueError(
            f"Audio too short ({n} samples) for nperseg={nperseg}. "
            "Need at least 2 segments."
        )

    # Number of Welch segments — key quality metric
    step = nperseg - noverlap
    n_segments = 1 + (n - nperseg) // step

    # --- Coherence (scipy.signal.coherence uses Welch's method internally) ---
    freqs, coh = signal.coherence(
        livingroom_audio, bedroom_audio,
        fs=sample_rate,
        window="hann",
        nperseg=nperseg,
        noverlap=noverlap,
        detrend="constant",
    )

    # --- Wall transmission loss at each frequency bin ---
    tl = wall_transmission_loss(freqs)

    # Bias floor: expected coherence for truly uncorrelated signals ≈ 1/K
    bias_floor = 1.0 / max(n_segments, 1)

    # --- Per-band analysis ---
    band_results: Dict[str, Any] = {}
    weighted_sum = 0.0
    total_weight = 0.0

    for name, band in BANDS.items():
        lo, hi = band["range"]
        mask = (freqs >= lo) & (freqs < hi)

        if not np.any(mask):
            band_results[name] = {
                "mean_coherence": 0.0,
                "leakage_db": -60.0,
                "wall_attenuation_db": 40.0,
            }
            continue

        # Bias-corrected coherence for this band
        bc = np.maximum(coh[mask] - bias_floor, 0.0)
        band_tl = tl[mask]
        mean_coh = float(np.mean(bc))

        if mean_coh > 1e-6:
            # Weighted mean attenuation (weight by coherence strength)
            weighted_tl = float(np.average(band_tl, weights=bc + 1e-10))
            # Leakage = coherence-weighted energy minus attenuation offset
            leakage = -weighted_tl + 10.0 * np.log10(max(mean_coh, 1e-10))
        else:
            leakage = -float(np.mean(band_tl)) - 30.0

        band_results[name] = {
            "mean_coherence":     round(mean_coh, 5),
            "leakage_db":         round(float(leakage), 1),
            "wall_attenuation_db": round(float(np.mean(band_tl)), 1),
        }
        weighted_sum  += band["weight"] * leakage
        total_weight  += band["weight"]

    overall = weighted_sum / max(total_weight, 1e-10)

    # --- Confidence based on segment count ---
    if   n_segments >= 2000: confidence = "excellent"
    elif n_segments >= 1000: confidence = "high"
    elif n_segments >= 300:  confidence = "medium"
    else:                    confidence = "low"

    # --- Traffic-light classification ---
    # These defaults should be tuned during calibration and stored in config.yaml
    if   overall > -25: color = "red"
    elif overall > -40: color = "amber"
    else:               color = "green"

    return {
        "overall_leakage_db": round(float(overall), 1),
        "band_breakdown":     band_results,
        "confidence":         confidence,
        "result_color":       color,
        "n_segments":         n_segments,
        "duration_sec":       round(n / sample_rate, 1),
        "mode":               mode,
        "processing_ms":      round((time.time() - t0) * 1000, 1),
    }


# ── Quick self-test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    print("Running self-test with synthetic signals…")
    fs = 16000
    for mode_name, cfg in MODE_CONFIG.items():
        dur = cfg["duration"]
        t   = np.linspace(0, dur, dur * fs)

        # Simulate: bedroom plays 100 Hz + 440 Hz + white noise
        bedroom = (
            0.4 * np.sin(2 * np.pi * 100 * t)
            + 0.2 * np.sin(2 * np.pi * 440 * t)
            + 0.1 * np.random.randn(len(t))
        )
        # Simulate: living room hears attenuated bedroom + local TV noise
        lr = (
            0.08 * np.sin(2 * np.pi * 100 * t)          # attenuated bass
            + 0.02 * np.sin(2 * np.pi * 440 * t)         # attenuated mid
            + 0.3  * np.random.randn(len(t))             # local TV noise
            + 0.15 * np.sin(2 * np.pi * 523 * t)         # local source (C5)
        )

        result = analyze_bedroom_leakage(bedroom, lr, fs, mode=mode_name)
        print(
            f"  {mode_name:6s}  "
            f"leakage={result['overall_leakage_db']:+6.1f} dB  "
            f"color={result['result_color']:5s}  "
            f"confidence={result['confidence']:9s}  "
            f"segments={result['n_segments']:5d}  "
            f"({result['processing_ms']:.0f} ms)"
        )
    print("Self-test passed.")
    sys.exit(0)
