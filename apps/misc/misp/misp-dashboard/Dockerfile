FROM python:3.14-slim

ARG MISP_DASHBOARD_REF=3c1125083aba83ea1ba049dfed625a2798a8878d

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends git ca-certificates curl; \
    rm -rf /var/lib/apt/lists/*; \
    git clone https://github.com/MISP/MISP-Dashboard.git /opt/MISP-Dashboard; \
    cd /opt/MISP-Dashboard; \
    git checkout "${MISP_DASHBOARD_REF}"; \
    pip install --no-cache-dir -r requirements.txt; \
    mkdir -p /opt/MISP-Dashboard/logs /opt/MISP-Dashboard/data/GeoLite2-City; \
    curl -fsSL https://raw.githubusercontent.com/P3TERX/GeoLite.mmdb/download/GeoLite2-City.mmdb \
      -o /opt/MISP-Dashboard/data/GeoLite2-City/GeoLite2-City.mmdb

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /opt/MISP-Dashboard
ENTRYPOINT ["/entrypoint.sh"]
