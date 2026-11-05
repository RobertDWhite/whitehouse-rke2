FROM python:3.15-slim@sha256:f0aecb335180611a3a000c45def529ec8ee768d5d26ff24bae4dfb70594a9121

# Pull current Debian security updates (openssl, perl-base, ...) on top of the base.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY digest.py ./digest.py

ENTRYPOINT ["python", "/app/digest.py"]
