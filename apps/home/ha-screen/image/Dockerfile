# Renders a Home Assistant dashboard in Chromium on a virtual display and publishes it as a
# live HLS stream (for TVs/streaming sticks that can play video but cannot open a web page).
FROM mcr.microsoft.com/playwright/python:v1.65.2-noble@sha256:38dd67fc654b56b3cba611e4c8eb71cd33750e1cd29eea2a5dc00b484817a75f
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
