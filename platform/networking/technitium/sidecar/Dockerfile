# Sidecar base for the Technitium zone-importer and metrics-exporter.
# Deps are baked in so the sidecars never runtime-`pip install` through the
# VPN proxy on startup (that hang used to flip pods NotReady and withdraw the
# DNS VIP). The scripts talk only to the local Technitium API, so this image
# needs no network at all once built.
FROM python:3.15-slim@sha256:e97b88e416c3c01eb427b21fd8cd3ebf006e6ee38afcfcc3cae1c54ee2ab0a75

# Pull current Debian security updates (perl-base, openssl, ...) on top of the base.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# requests/urllib3/PyYAML: zone-importer, metrics-exporter, unifi-sync.
# dnspython: the DNS self-heal watchdog probes each pod's :53 for real serving.
# certifi pinned to the CVE-fixed bundle (requests would otherwise pull whatever is latest).
RUN pip install --no-cache-dir requests urllib3 PyYAML dnspython "certifi>=2026.10.18" \
    && python -c "import requests, urllib3, yaml, dns.resolver; print('deps ok')"
