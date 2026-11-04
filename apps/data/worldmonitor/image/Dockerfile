# Patch layer over the locally built worldmonitor 1.0.0 (koala73/worldmonitor, exact source commit not recorded).
FROM registry.internal.white.fm/worldmonitor:1.0.0@sha256:920e5b85654be9618299c92490f189160fbc774d9f45daa2348090d6a8d611ed
USER root
# OS fixes (nginx CVE-2026-42945 etc.); npm/npx/corepack are unused at runtime
# (supervisord runs nginx + node /app/local-api-server.mjs) and carry npm's bundled tar.
RUN apk upgrade --no-cache \
 && rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
           /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
 && rm -rf /root/.npm /tmp/*
USER 101
LABEL org.opencontainers.image.source="https://github.com/koala73/worldmonitor" \
      org.opencontainers.image.base.name="registry.internal.white.fm/worldmonitor:1.0.0"
