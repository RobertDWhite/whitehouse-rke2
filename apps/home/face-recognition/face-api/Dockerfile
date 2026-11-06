# face-api: CompreFace-compatible recognition API (see README.md).
FROM python:3.15-slim-trixie@sha256:e97b88e416c3c01eb427b21fd8cd3ebf006e6ee38afcfcc3cae1c54ee2ab0a75 AS models
ADD --checksum=sha256:80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f \
    https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip /tmp/buffalo_l.zip
RUN python -c "import zipfile; zipfile.ZipFile('/tmp/buffalo_l.zip').extractall('/models', ['det_10g.onnx', 'w600k_r50.onnx'])"

FROM python:3.15-slim-trixie@sha256:e97b88e416c3c01eb427b21fd8cd3ebf006e6ee38afcfcc3cae1c54ee2ab0a75
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt
COPY --from=models /models /models
COPY app /srv/app
WORKDIR /srv
ENV PYTHONUNBUFFERED=1 MODEL_DIR=/models DATA_DIR=/data
USER 1000:1000
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
