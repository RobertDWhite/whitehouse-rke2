# Sidecar base for the Technitium zone-importer and metrics-exporter.
# Deps are baked in so the sidecars never runtime-`pip install` through the
# VPN proxy on startup (that hang used to flip pods NotReady and withdraw the
# DNS VIP). The scripts talk only to the local Technitium API, so this image
# needs no network at all once built.
FROM python:3.14-slim

# requests/urllib3/PyYAML: zone-importer, metrics-exporter, unifi-sync.
# dnspython: the DNS self-heal watchdog probes each pod's :53 for real serving.
RUN pip install --no-cache-dir requests urllib3 PyYAML dnspython \
    && python -c "import requests, urllib3, yaml, dns.resolver; print('deps ok')"
