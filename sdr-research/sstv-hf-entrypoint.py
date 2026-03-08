#!/usr/bin/env python3
"""HF SSTV decoder — connects to RX888 via SoapyRemote, tunes to HF SSTV frequencies,
USB-demodulates the audio, and decodes images with slowrx-cli.

NOTE: The RX888 SoapyRemote server (rx888-soapy) only allows one client at a time.
Scale openwebrxplus to 0 before running this pod at replicas > 0:
  kubectl scale deploy openwebrxplus -n sdr-research --replicas=0
  kubectl scale deploy sstv-hf-decoder -n sdr-research --replicas=1

HF SSTV frequencies (USB mode):
  14.230 MHz — 20m (most active worldwide)
  21.340 MHz — 15m
  28.680 MHz — 10m
   7.171 MHz — 40m (less common)
"""

import os
import sys
import time
import wave
import tempfile
import subprocess
from typing import List

import numpy as np
from scipy import signal as sp_signal

# ── Config ────────────────────────────────────────────────────────────────────
SOAPY_REMOTE  = os.getenv("SOAPY_REMOTE_RX888",
                           "rx888-soapy.sdr-research.svc.cluster.local:55133")
OUTPUT_DIR    = os.getenv("SSTV_OUTPUT_DIR", "/data/images/sstv")
SAMPLE_RATE   = int(os.getenv("HF_SAMPLE_RATE",   "2048000"))   # 2 MSPS from RX888
AUDIO_RATE    = int(os.getenv("HF_AUDIO_RATE",     "16000"))     # 2048000/128 = 16000 Hz
DWELL_SEC     = int(os.getenv("HF_DWELL_SEC",      "120"))       # seconds per frequency
GAIN          = float(os.getenv("HF_GAIN",          "20"))

HF_SSTV_FREQS: List[int] = [
    int(x) for x in
    os.getenv("HF_SSTV_FREQS",
              "14230000,21340000,28680000,7171000").split(",")
]


def usb_demod(iq: np.ndarray, fs: int, audio_rate: int) -> np.ndarray:
    """Upper-sideband demodulation from complex baseband IQ samples."""
    # For USB: keep only the upper (positive-frequency) sideband.
    # Analytic signal approach: zero the negative-freq FFT bins, IFFT → real part.
    n = len(iq)
    spec = np.fft.fft(iq)
    # Zero DC and negative frequencies
    spec[n // 2:] = 0
    spec[0] = 0
    analytic = np.fft.ifft(spec)
    audio_full = np.real(analytic) * 2.0   # ×2 to compensate for zeroed half

    # Bandpass 300–3000 Hz (voice/SSTV sub-tone range)
    nyq = fs / 2
    sos = sp_signal.butter(5, [300 / nyq, 3000 / nyq], btype="bandpass", output="sos")
    audio_bp = sp_signal.sosfilt(sos, audio_full)

    # Decimate to audio_rate
    decim = fs // audio_rate
    audio = sp_signal.decimate(audio_bp, decim, ftype="fir", zero_phase=True)
    return audio.astype(np.float32)


def write_wav(audio: np.ndarray, path: str, rate: int) -> None:
    # Normalise and write 16-bit PCM WAV
    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = audio / peak * 0.9
    pcm = (audio * 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm.tobytes())


def run_slowrx(wav_path: str, freq_hz: int, audio_rate: int) -> None:
    from PIL import Image
    ts_ms = int(time.time() * 1000)
    with tempfile.TemporaryDirectory() as tmp:
        bmp_path = os.path.join(tmp, "result.bmp")
        result = subprocess.run(
            ["slowrx-cli", "-v", "-r", str(audio_rate), "-o", bmp_path, wav_path],
            capture_output=True, text=True, timeout=300
        )
        if not os.path.exists(bmp_path):
            print(f"[SSTV-HF] No image decoded @ {freq_hz/1e6:.3f} MHz", flush=True)
            return
        dst = os.path.join(OUTPUT_DIR, f"sstv_{freq_hz}_{ts_ms}.png")
        try:
            Image.open(bmp_path).save(dst, "PNG")
            print(f"[SSTV-HF] Decoded image @ {freq_hz/1e6:.3f} MHz → {os.path.basename(dst)}",
                  flush=True)
        except Exception as e:
            print(f"[SSTV-HF] BMP→PNG failed: {e}", flush=True)


def collect_and_decode(sdr, freq_hz: int) -> None:
    import SoapySDR

    print(f"[SSTV-HF] Tuning to {freq_hz/1e6:.3f} MHz for {DWELL_SEC}s", flush=True)
    sdr.setFrequency(SoapySDR.SOAPY_SDR_RX, 0, freq_hz)
    time.sleep(0.5)  # PLL settle

    buf_size  = 65536
    n_samples = SAMPLE_RATE * DWELL_SEC
    chunks: List[np.ndarray] = []
    total = 0

    rx_stream = sdr.setupStream(SoapySDR.SOAPY_SDR_RX, SoapySDR.SOAPY_SDR_CF32)
    sdr.activateStream(rx_stream)
    try:
        buf = np.zeros(buf_size, dtype=np.complex64)
        while total < n_samples:
            sr = sdr.readStream(rx_stream, [buf], buf_size, timeoutUs=1_000_000)
            if sr.ret > 0:
                chunks.append(buf[:sr.ret].copy())
                total += sr.ret
    finally:
        sdr.deactivateStream(rx_stream)
        sdr.closeStream(rx_stream)

    if not chunks:
        print(f"[SSTV-HF] No samples collected at {freq_hz/1e6:.3f} MHz", flush=True)
        return

    iq = np.concatenate(chunks)
    audio = usb_demod(iq, SAMPLE_RATE, AUDIO_RATE)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav_path = f.name
    try:
        write_wav(audio, wav_path, AUDIO_RATE)
        run_slowrx(wav_path, freq_hz, AUDIO_RATE)
    finally:
        os.unlink(wav_path)


def main() -> None:
    try:
        import SoapySDR
    except ImportError:
        print("[SSTV-HF] python3-soapysdr not available — exiting", flush=True)
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"[SSTV-HF] HF SSTV decoder started", flush=True)
    print(f"[SSTV-HF] Remote: {SOAPY_REMOTE}", flush=True)
    print(f"[SSTV-HF] Freqs:  {[f'{f/1e6:.3f} MHz' for f in HF_SSTV_FREQS]}", flush=True)

    while True:
        try:
            sdr = SoapySDR.Device({
                "driver": "remote",
                "remote": f"tcp://{SOAPY_REMOTE}",
            })
            sdr.setSampleRate(SoapySDR.SOAPY_SDR_RX, 0, SAMPLE_RATE)
            sdr.setGain(SoapySDR.SOAPY_SDR_RX, 0, GAIN)
            print(f"[SSTV-HF] Connected to {SOAPY_REMOTE}", flush=True)

            while True:
                for freq in HF_SSTV_FREQS:
                    collect_and_decode(sdr, freq)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[SSTV-HF] Error: {e} — retrying in 30s", flush=True)
            time.sleep(30)


if __name__ == "__main__":
    main()
