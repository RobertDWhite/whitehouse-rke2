# Renders a Home Assistant dashboard in Chromium on a virtual display and publishes it as a
# live HLS stream (for TVs/streaming sticks that can play video but cannot open a web page).
FROM mcr.microsoft.com/playwright/python:v1.64.1-noble@sha256:330d401ae23c905896a0c26c66c36fc971b621519e7b11b435a8328d7bf6e031
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && apt-get install -y --no-install-recommends xvfb ffmpeg fonts-noto-color-emoji tini \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir playwright==1.64.1 "certifi>=2026.10.18" \
 && rm -rf /root/.cache
# /root/.cache held a stale virtualenv wheel cache from the base image (old pip vendoring a
# vulnerable certifi); nothing at runtime uses it.
COPY stream.py /app/stream.py
USER pwuser
ENTRYPOINT ["/usr/bin/tini", "--", "python3", "-u", "/app/stream.py"]
