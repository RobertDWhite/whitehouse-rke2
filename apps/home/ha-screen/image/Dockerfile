# Renders a Home Assistant dashboard in Chromium on a virtual display and publishes it as a
# live HLS stream (for TVs/streaming sticks that can play video but cannot open a web page).
FROM mcr.microsoft.com/playwright/python:v1.64.1-noble
RUN apt-get update \
 && apt-get install -y --no-install-recommends xvfb ffmpeg fonts-noto-color-emoji tini \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir playwright==1.64.1
COPY stream.py /app/stream.py
USER pwuser
ENTRYPOINT ["/usr/bin/tini", "--", "python3", "-u", "/app/stream.py"]
