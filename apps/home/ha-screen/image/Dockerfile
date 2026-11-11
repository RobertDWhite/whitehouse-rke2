# Renders a Home Assistant dashboard in Chromium on a virtual display and publishes it as a
# live HLS stream (for TVs/streaming sticks that can play video but cannot open a web page).
FROM mcr.microsoft.com/playwright/python:v1.65.0-noble@sha256:f9294526294a7bdfb8ca8aa7d815c049125a10e73b65d47fb9a80f5d74f284a6
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
