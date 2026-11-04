FROM python:3.15-slim@sha256:8c13a3f37a6a5b71498228e902c4b9f51c1f7900d49fabf71f9a650c6120e0d0

ARG MISP_DASHBOARD_REF=3c1125083aba83ea1ba049dfed625a2798a8878d

# git/curl are only needed to fetch the source and GeoLite DB; they are purged
# afterwards so they (and git's perl dependency) don't ship in the image.
RUN set -eux; \
    apt-get update; \
    apt-get upgrade -y --no-install-recommends; \
    apt-get install -y --no-install-recommends git ca-certificates curl; \
    git clone https://github.com/MISP/MISP-Dashboard.git /opt/MISP-Dashboard; \
    cd /opt/MISP-Dashboard; \
    git checkout "${MISP_DASHBOARD_REF}"; \
    pip install --no-cache-dir -r requirements.txt; \
    pip install --no-cache-dir --upgrade "certifi>=2026.10.18"; \
    mkdir -p /opt/MISP-Dashboard/logs /opt/MISP-Dashboard/data/GeoLite2-City; \
    curl -fsSL https://raw.githubusercontent.com/P3TERX/GeoLite.mmdb/download/GeoLite2-City.mmdb \
      -o /opt/MISP-Dashboard/data/GeoLite2-City/GeoLite2-City.mmdb; \
    rm -rf /opt/MISP-Dashboard/.git; \
    apt-get purge -y --auto-remove git curl; \
    rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /opt/MISP-Dashboard
ENTRYPOINT ["/entrypoint.sh"]
