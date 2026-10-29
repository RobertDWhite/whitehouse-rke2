#!/bin/bash
# Builds the vLLM + NeMo Speech venv on the Longhorn PVC (/data) and fetches the
# NVFP4 checkpoint onto node-50's local disk (/models). Each step is skipped once
# its marker matches, so a normal pod start costs a few seconds. Bump
# NEMO_COMMIT / MODEL_REVISION to rebuild.
set -euo pipefail
cd /data

marker=/data/.venv-${NEMO_COMMIT}
if [ ! -f "$marker" ]; then
  rm -rf /data/nemo-speech /data/venv /data/.venv-*
  git clone --filter=blob:none https://github.com/NVIDIA-NeMo/Speech.git /data/nemo-speech
  git -C /data/nemo-speech checkout --quiet "$NEMO_COMMIT"
  uv venv --python 3.13 /data/venv
  (cd /data/nemo-speech &&
    UV_PROJECT_ENVIRONMENT=/data/venv uv sync --extra asr --extra vllm --locked --no-install-project &&
    uv pip install --python /data/venv/bin/python --no-deps -e . &&
    uv pip install --python /data/venv/bin/python --no-deps av==19.0.2 opencv-python-headless==5.0.0.93)
  touch "$marker"
fi

model_marker="/models/.model-${MODEL_REVISION}"
if [ ! -f "$model_marker" ]; then
  /data/venv/bin/hf download "$MODEL_REPO" --revision "$MODEL_REVISION" --local-dir "$MODEL_DIR"
  rm -f /models/.model-*
  touch "$model_marker"
fi

# The first prototype kept the checkpoint on Longhorn (8-12 MB/s: a 15 min load).
rm -rf /data/models
mkdir -p /data/tmp /data/cache
rm -rf /data/tmp/*
