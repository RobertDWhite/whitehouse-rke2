#!/usr/bin/env python3
"""HFDL decoder wrapper for sdr-research.

Runs dumphfdl connected to rx888-soapy via SoapyRemote and writes one JSON
file per decoded frame to HFDL_OUTPUT_DIR (default /data/text/hfdl).
The sdr-viewer API indexer picks these up as mode='hfdl' recordings.

NOTE: The RX888 SoapyRemote server (rx888-soapy) only accepts one client at
a time.  Scale openwebrxplus to 0 replicas when running this decoder, or
invest in a second HF SDR.
"""

import json
import os
import subprocess
import sys
import time

SOAPY_REMOTE = os.getenv("SOAPY_REMOTE", "rx888-soapy.sdr-research.svc.cluster.local:55132")
OUTPUT_DIR = os.getenv("HFDL_OUTPUT_DIR", "/data/text/hfdl")
# Frequencies to monitor in Hz — coverage for New York and San Francisco HFDL GS
FREQUENCIES = os.getenv(
    "HFDL_FREQUENCIES",
    "8912000 8927000 10081000 11384000 13303000",
).split()

os.makedirs(OUTPUT_DIR, exist_ok=True)

print(f"[HFDL] Starting dumphfdl | remote={SOAPY_REMOTE}", flush=True)
print(f"[HFDL] Frequencies: {' '.join(FREQUENCIES)}", flush=True)
print(f"[HFDL] Output dir : {OUTPUT_DIR}", flush=True)

cmd = [
    "dumphfdl",
    "--soapysdr", f"driver=remote,remote={SOAPY_REMOTE}",
    "--output", "decoded:json:stdout",
] + FREQUENCIES

proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=sys.stderr, text=True, bufsize=1)

for line in proc.stdout:
    line = line.strip()
    if not line:
        continue
    try:
        frame = json.loads(line)
        t = frame.get("t", {})
        freq = int(frame.get("freq", 0))
        ts_sec = t.get("sec", int(time.time()))
        ts_usec = t.get("usec", 0)
        ts_ms = ts_sec * 1000 + (ts_usec // 1000)
        filename = f"hfdl_{freq}_{ts_ms}.json"
        filepath = os.path.join(OUTPUT_DIR, filename)
        with open(filepath, "w") as f:
            json.dump(frame, f)
        print(f"[HFDL] {filename}", flush=True)
    except (json.JSONDecodeError, KeyError, ValueError, OSError) as exc:
        print(f"[HFDL] Error: {exc}: {line[:100]}", flush=True)

rc = proc.wait()
print(f"[HFDL] dumphfdl exited with code {rc}", flush=True)
sys.exit(rc)
