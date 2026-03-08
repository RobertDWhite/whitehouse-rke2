#!/usr/bin/env python3
"""VHF/UHF SSTV decoder — watches WAV files from unified-sdr and decodes SSTV images.

WAV files are named {freq_hz}_{timestamp_ms}.wav in AUDIO_DIR.
Matching files at SSTV frequencies are decoded by slowrx-cli → PNGs in OUTPUT_DIR.

Monitors:
  144.500 MHz — 2m SSTV calling frequency (worldwide)
  145.800 MHz — ISS SSTV downlink (ARISS events)
  432.100 MHz — 70cm SSTV calling frequency
"""

import os
import re
import glob
import time
import shutil
import tempfile
import subprocess

from PIL import Image

AUDIO_DIR   = os.getenv("AUDIO_DIR",       "/data/audio/voice")
OUTPUT_DIR  = os.getenv("SSTV_OUTPUT_DIR", "/data/images/sstv")
AUDIO_RATE  = int(os.getenv("AUDIO_RATE",   "48000"))  # unified-sdr AUDIO_RATE_FM
MIN_AGE_SEC = int(os.getenv("MIN_FILE_AGE_SEC", "15"))
POLL_SEC    = int(os.getenv("POLL_INTERVAL_SEC", "10"))

# (low_hz, high_hz, label) — generous ±25 kHz tolerance
SSTV_RANGES = [
    (144_475_000, 144_525_000, "2m"),
    (145_775_000, 145_825_000, "ISS"),
    (432_075_000, 432_125_000, "70cm"),
]

_seen: set[str] = set()


def classify(freq_hz: int) -> str | None:
    for lo, hi, label in SSTV_RANGES:
        if lo <= freq_hz <= hi:
            return label
    return None


def bmp_to_png(bmp_path: str, png_path: str) -> None:
    """Convert BMP output from slowrx-cli to PNG."""
    img = Image.open(bmp_path)
    img.save(png_path, "PNG")


def decode_wav(wav_path: str, freq_hz: int, label: str) -> None:
    ts_ms = int(time.time() * 1000)
    with tempfile.TemporaryDirectory() as tmp:
        bmp_path = os.path.join(tmp, "result.bmp")
        result = subprocess.run(
            ["slowrx-cli", "-v", "-r", str(AUDIO_RATE),
             "-o", bmp_path, wav_path],
            capture_output=True, text=True, timeout=300
        )

        if not os.path.exists(bmp_path):
            stderr_snip = result.stderr.strip()[:120] if result.stderr else ""
            stdout_snip = result.stdout.strip()[:120] if result.stdout else ""
            print(f"[SSTV] No image decoded from {os.path.basename(wav_path)} "
                  f"({stderr_snip or stdout_snip})", flush=True)
            return

        dst = os.path.join(OUTPUT_DIR, f"sstv_{freq_hz}_{ts_ms}.png")
        try:
            bmp_to_png(bmp_path, dst)
            print(f"[SSTV] Decoded {label} image → {os.path.basename(dst)}", flush=True)
        except Exception as e:
            print(f"[SSTV] BMP→PNG conversion failed: {e}", flush=True)


def process(wav_path: str) -> None:
    if wav_path in _seen:
        return

    base = os.path.basename(wav_path)
    m = re.match(r'^(\d+)_\d+\.wav$', base)
    if not m:
        return

    freq_hz = int(m.group(1))
    label = classify(freq_hz)
    if label is None:
        return

    try:
        age = time.time() - os.path.getmtime(wav_path)
    except OSError:
        return
    if age < MIN_AGE_SEC:
        return

    _seen.add(wav_path)
    print(f"[SSTV] Processing {base} ({label}, {freq_hz/1e6:.3f} MHz)", flush=True)
    try:
        decode_wav(wav_path, freq_hz, label)
    except subprocess.TimeoutExpired:
        print(f"[SSTV] Timeout on {base}", flush=True)
        _seen.discard(wav_path)
    except Exception as e:
        print(f"[SSTV] Error on {base}: {e}", flush=True)
        _seen.discard(wav_path)


def main() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ranges_str = ", ".join(f"{lo/1e6:.3f}–{hi/1e6:.3f} MHz ({lbl})"
                           for lo, hi, lbl in SSTV_RANGES)
    print(f"[SSTV] VHF/UHF decoder started", flush=True)
    print(f"[SSTV] Watching: {AUDIO_DIR}", flush=True)
    print(f"[SSTV] Ranges:   {ranges_str}", flush=True)
    print(f"[SSTV] Output:   {OUTPUT_DIR}", flush=True)

    while True:
        for wav in glob.glob(os.path.join(AUDIO_DIR, "*.wav")):
            process(wav)
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
