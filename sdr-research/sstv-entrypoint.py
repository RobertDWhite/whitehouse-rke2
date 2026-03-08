#!/usr/bin/env python3
"""VHF/UHF SSTV decoder — watches WAV files from unified-sdr and decodes SSTV images.

WAV files are named {freq_hz}_{timestamp_ms}.wav in AUDIO_DIR.
Matching files at SSTV frequencies are decoded by slowrx → PNGs in OUTPUT_DIR.

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

AUDIO_DIR   = os.getenv("AUDIO_DIR",       "/data/audio/voice")
OUTPUT_DIR  = os.getenv("SSTV_OUTPUT_DIR", "/data/images/sstv")
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


def decode_wav(wav_path: str, freq_hz: int, label: str) -> None:
    ts_ms = int(time.time() * 1000)
    with tempfile.TemporaryDirectory() as tmp:
        before = set(os.listdir(tmp))
        env = {**os.environ, "SDL_VIDEODRIVER": "offscreen", "SDL_AUDIODRIVER": "dummy"}
        result = subprocess.run(
            ["slowrx", "-f", wav_path, "-d", tmp],
            env=env, capture_output=True, text=True, timeout=300
        )
        after = set(os.listdir(tmp))
        new_files = after - before

        if not new_files:
            print(f"[SSTV] No image decoded from {os.path.basename(wav_path)} "
                  f"({result.stderr.strip()[:120]})", flush=True)
            return

        for fname in new_files:
            if not fname.lower().endswith(".png"):
                continue
            src = os.path.join(tmp, fname)
            dst = os.path.join(OUTPUT_DIR, f"sstv_{freq_hz}_{ts_ms}.png")
            shutil.move(src, dst)
            print(f"[SSTV] Decoded {label} image → {os.path.basename(dst)}", flush=True)
            ts_ms += 1  # avoid collision if multiple images


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
