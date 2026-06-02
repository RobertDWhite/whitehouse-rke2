#!/usr/bin/env python3
"""ACARS decoder wrapper for sdr-research.

Runs acarsdec connected to the Airspy Mini via SoapyRemote and writes one JSON
file per decoded message to ACARS_OUTPUT_DIR (default /data/text/acars).
The sdr-viewer API indexer picks these up as mode='acars' recordings.

acarsdec outputs one JSON line per decoded ACARS message to stdout when using
the -j flag.
"""

import json
import os
import subprocess
import sys
import time

SOAPY_REMOTE = os.getenv("SOAPY_REMOTE", "airspy-soapy.sdr-research.svc.cluster.local:55132")
OUTPUT_DIR = os.getenv("ACARS_OUTPUT_DIR", "/data/text/acars")
GAIN = os.getenv("ACARS_GAIN", "18")

# Standard VHF ACARS frequencies (MHz) — acarsdec expects MHz
FREQUENCIES = os.getenv(
    "ACARS_FREQUENCIES",
    "129.125 130.025 130.425 130.450 131.125 131.550",
).split()

os.makedirs(OUTPUT_DIR, exist_ok=True)

print(f"[ACARS] Starting acarsdec | remote={SOAPY_REMOTE}", flush=True)
print(f"[ACARS] Frequencies: {' '.join(FREQUENCIES)} MHz", flush=True)
print(f"[ACARS] Output dir : {OUTPUT_DIR}", flush=True)

# acarsdec -L connects via SoapySDR (which connects to SoapyRemote → Airspy)
# -j enables JSON output to stdout
# -o <gain> sets overall gain
cmd = [
    "acarsdec",
    "-j",
    "-L", f"driver=remote,remote=tcp://{SOAPY_REMOTE},remote:driver=airspy",
    "-o", GAIN,
]
for freq in FREQUENCIES:
    cmd.extend(["-f", freq])

print(f"[ACARS] Command: {' '.join(cmd)}", flush=True)

proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=sys.stderr, text=True, bufsize=1)

for line in proc.stdout:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
        # acarsdec JSON includes: flight, reg, label, text, freq, channel, etc.
        freq_mhz = msg.get("freq", 0)
        flight = msg.get("flight", "UNKNOWN").strip()
        ts = int(time.time() * 1000)
        freq_hz = int(float(freq_mhz) * 1_000_000) if freq_mhz else 0
        filename = f"acars_{freq_hz}_{ts}.json"
        filepath = os.path.join(OUTPUT_DIR, filename)
        with open(filepath, "w") as f:
            json.dump(msg, f)
        label = msg.get("label", "")
        text_preview = (msg.get("text", "") or "")[:60]
        print(f"[ACARS] {filename} | {flight} | {label} | {text_preview}", flush=True)
    except (json.JSONDecodeError, KeyError, ValueError, OSError) as exc:
        print(f"[ACARS] Error: {exc}: {line[:120]}", flush=True)

rc = proc.wait()
print(f"[ACARS] acarsdec exited with code {rc}", flush=True)
sys.exit(rc)
