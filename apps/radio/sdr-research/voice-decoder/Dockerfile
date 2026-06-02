FROM registry.white.fm/comfyui-nvidia:0.3.58-cuda13.1-dgx-arm64-20260223-1

USER root

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    CTRANSLATE2_ROOT=/usr/local \
    PYTHONUNBUFFERED=1

ARG CTRANSLATE2_VERSION=v4.7.1

WORKDIR /tmp/build

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg \
      git \
      build-essential \
      cmake \
      ninja-build \
      python3-dev \
      libopenblas-dev \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN git clone --recursive --branch ${CTRANSLATE2_VERSION} https://github.com/OpenNMT/CTranslate2.git

WORKDIR /tmp/build/CTranslate2

RUN cmake -S . -B build -GNinja \
      -DCMAKE_BUILD_TYPE=Release \
      -DBUILD_SHARED_LIBS=ON \
      -DWITH_MKL=OFF \
      -DWITH_DNNL=OFF \
      -DWITH_CUDA=ON \
      -DWITH_CUDNN=ON \
      -DOPENMP_RUNTIME=COMP \
      -DCUDA_ARCH_LIST=Ampere \
      -DCUDA_NVCC_FLAGS="-gencode arch=compute_120,code=sm_120 -gencode arch=compute_120,code=compute_120" \
 && cmake --build build --parallel \
 && cmake --install build \
 && ldconfig

WORKDIR /tmp/build/CTranslate2/python

RUN python3 -m pip install --no-cache-dir --break-system-packages -r install_requirements.txt \
 && python3 setup.py bdist_wheel \
 && mkdir -p /tmp/wheels \
 && cp dist/ctranslate2-*.whl /tmp/wheels/

RUN python3 -m pip install --no-cache-dir --break-system-packages faster-whisper==1.2.1 \
 && python3 -m pip install --no-cache-dir --break-system-packages --force-reinstall /tmp/wheels/ctranslate2-*.whl \
 && python3 -c 'import ctranslate2; print("ctranslate2", ctranslate2.__version__)'

RUN rm -rf /tmp/build /tmp/wheels

WORKDIR /
