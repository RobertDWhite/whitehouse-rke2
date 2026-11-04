# Security patch layer over upstream NousResearch/hermes-agent v0.18.2 (2026.7.7.2,
# rev 9de9c25f), built unmodified. Behaviour unchanged; only package versions move.
ARG BASE=registry.internal.white.fm/hermes-agent:v2026.7.7.2@sha256:9c841866021c54c4596849f6135717e8a4d52ba510b7f52c50aef1de1a283973
FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv
FROM ${BASE}
USER root
# Debian security/point-release fixes (perl, mesa, glib, mbedtls, brotli, docker-cli, ...)
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::=--force-confold upgrade \
 && apt-get clean && rm -rf /var/lib/apt/lists/*
# uv/uvx: newer build without the vulnerable zip crate
COPY --from=uv /uv /uvx /usr/local/bin/
# Python deps in the app venv: same fixed versions upstream pins in v0.22.x
RUN uv pip install --python /opt/hermes/.venv/bin/python --no-cache \
      "pyjwt[crypto]==2.15.1" "anyio>=4.14.2" "brotlicffi==1.2.1.0" "certifi>=2026.10.18" "pillow==13.0.0"
# npm's bundled tar (CVE-2026-59873): npm 10.9.10 ships tar ^7.5.22
RUN npm install -g npm@10.9.10 && npm cache clean --force && rm -rf /root/.npm
LABEL org.opencontainers.image.source="https://github.com/NousResearch/hermes-agent" \
      org.opencontainers.image.revision="9de9c25f620ff7f1ce0fd5457d596052d5159596" \
      org.opencontainers.image.base.name="registry.internal.white.fm/hermes-agent:v2026.7.7.2"
