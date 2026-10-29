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
    uv pip install --python /data/venv/bin/python --no-deps av==19.0.2 opencv-python-headless==5.0.0.93 &&
    # FlashInfer JIT-compiles its NVFP4 GEMMs with this nvcc. The lock pairs
    # nvcc 13.4 with 13.0 runtime headers, which CCCL refuses to build
    # ("CUDA compiler and CUDA toolkit headers are incompatible"); 13.0 also
    # matches node-50's 580 driver.
    uv pip install --python /data/venv/bin/python --no-deps \
      "nvidia-cuda-nvcc==13.0.*" "nvidia-cuda-crt==13.0.*" "nvidia-nvvm==13.0.*")
  # FlashInfer links with -L$CUDA_HOME/lib64{,/stubs} -lcudart -lnvrtc -lcuda;
  # the wheels only ship versioned libraries under lib/. The stub points at the
  # driver library the nvidia runtime mounts into the container.
  cu=$(echo /data/venv/lib/python3*/site-packages/nvidia/cu13)
  ln -sfn lib "$cu/lib64"
  for f in "$cu"/lib/lib*.so.[0-9]*; do
    b=${f%%.so.*}.so
    [ -e "$b" ] || ln -s "$(basename "$f")" "$b"
  done
  mkdir -p "$cu/lib/stubs"
  ln -sf /usr/lib/x86_64-linux-gnu/libcuda.so.1 "$cu/lib/stubs/libcuda.so"
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
