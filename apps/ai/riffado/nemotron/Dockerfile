# Runtime for nemotron-transcribe. The venv (vLLM + NeMo Speech) and its
# uv-managed CPython live on the PVC, so the image only needs what setup.sh and
# FlashInfer's JIT use: bash, git, uv and a host C/C++ toolchain for nvcc.
# Replaces ghcr.io/astral-sh/uv:python3.13-bookworm (buildpack-deps carried
# ~170 critical CVEs in packages nothing here uses).
FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

FROM debian:trixie-slim@sha256:f6b08b09a05725f9431502d3b5714f5c4576e82c1a84fa1debe0c24a7e0c3ec4
RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends bash ca-certificates git gcc g++ libc6-dev make \
 && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /uvx /usr/local/bin/
USER 1000:1000
