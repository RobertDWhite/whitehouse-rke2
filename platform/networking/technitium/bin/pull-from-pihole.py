#!/usr/bin/env python3
"""Pull custom DNS records from every Pi-hole and write a sops-encrypted
Secret containing per-zone YAML files. Run this locally — the Secret is
committed to git and becomes the source of truth for Technitium.

    PIHOLE_PASSWORD=... ./bin/pull-from-pihole.py

Re-run any time you want to re-seed from Pi-hole. After cutover, edit the
zones via `sops technitium/35-zones-secret.sops.yaml` directly and stop
running this script.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import urllib3
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PIHOLE_HOSTS = [h.strip() for h in os.environ.get(
    "PIHOLE_HOSTS",
    "https://10.99.5.2,https://10.100.0.20,https://10.100.0.21",
).split(",") if h.strip()]
PIHOLE_PASS = os.environ["PIHOLE_PASSWORD"]
INTERNAL_TLDS = [t.strip().rstrip(".") for t in os.environ.get(
    "INTERNAL_TLDS",
    "internal.white.fm,internal.w3rdw.radio,internal.whitematter.tech",
).split(",") if t.strip()]

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH  = REPO_ROOT / "35-zones-secret.sops.yaml"

# Manual overrides — records whose value should NEVER be taken from Pi-hole.
# These records are managed in the Technitium cluster directly and the
# pull-from-pihole flow must not clobber them.
#   dns.internal.white.fm → the DNS server LB IP, used as the public
#                           "DNS server hostname" for router configs.
OVERRIDES = {
    "dns.internal.white.fm": "10.99.5.50",
}


def fetch(host):
    s = requests.Session()
    s.verify = False
    try:
        r = s.post(f"{host}/api/auth", json={"password": PIHOLE_PASS}, timeout=15)
        r.raise_for_status()
        sid = r.json()["session"]["sid"]
        headers = {"sid": sid}
        hosts = s.get(f"{host}/api/config/dns/hosts", headers=headers, timeout=15).json()
        cnames = s.get(f"{host}/api/config/dns/cnameRecords", headers=headers, timeout=15).json()
        try:
            s.delete(f"{host}/api/auth", headers=headers, timeout=10)
        except Exception:
            pass
    finally:
        s.close()

    a_records = []
    for entry in hosts.get("config", {}).get("dns", {}).get("hosts", []):
        parts = entry.split()
        if len(parts) < 2:
            continue
        ip = parts[0]
        for n in parts[1:]:
            a_records.append((n.lower(), ip))

    cname_records = []
    for entry in cnames.get("config", {}).get("dns", {}).get("cnameRecords", []):
        parts = [p.strip() for p in entry.split(",")]
        if len(parts) >= 2:
            cname_records.append((parts[0].lower(), parts[1].lower()))

    return a_records, cname_records


def best_tld(name):
    for tld in INTERNAL_TLDS:
        if name == tld or name.endswith("." + tld):
            return tld
    return None


def render_zone_yaml(zone, records):
    lines = [
        "# Source of truth for Technitium zone records.",
        "# Edit via: sops technitium/35-zones-secret.sops.yaml",
        f"zone: {zone}",
    ]
    if not records:
        lines.append("records: []")
    else:
        lines.append("records:")
        for r in sorted(records, key=lambda x: (x["type"], x["name"])):
            lines.append(
                f'  - {{name: {r["name"]}, type: {r["type"]}, value: {r["value"]}}}'
            )
    return "\n".join(lines) + "\n"


def render_secret_manifest(zone_yamls):
    lines = [
        "apiVersion: v1",
        "kind: Secret",
        "metadata:",
        "  name: technitium-zones",
        "  namespace: technitium",
        "type: Opaque",
        "stringData:",
    ]
    for zone, content in zone_yamls.items():
        lines.append(f"  {zone}.yaml: |")
        for line in content.splitlines():
            lines.append(f"    {line}")
    return "\n".join(lines) + "\n"


def sops_encrypt_in_place(path):
    sops = shutil.which("sops")
    if not sops:
        sys.exit("error: sops not found in PATH")
    subprocess.run([sops, "-e", "-i", str(path)], check=True)


def main():
    a_all, c_all = {}, {}
    for h in PIHOLE_HOSTS:
        try:
            a, c = fetch(h)
            for n, ip in a:
                a_all[n] = ip
            for alias, target in c:
                c_all[alias] = target
            print(f"[{h}] {len(a)} A, {len(c)} CNAME")
        except Exception as e:
            print(f"[{h}] FAIL: {e}", file=sys.stderr)

    # Apply overrides — Pi-hole values for these names are ignored.
    for n, ip in OVERRIDES.items():
        a_all[n] = ip

    by_zone = {tld: [] for tld in INTERNAL_TLDS}
    skipped = 0
    for n, ip in a_all.items():
        tld = best_tld(n)
        if tld is None:
            skipped += 1
            continue
        by_zone[tld].append({"name": n, "type": "A", "value": ip})
    for alias, target in c_all.items():
        tld = best_tld(alias)
        if tld is None:
            skipped += 1
            continue
        by_zone[tld].append({"name": alias, "type": "CNAME", "value": target})

    if skipped:
        print(f"skipped {skipped} records outside internal TLDs", file=sys.stderr)

    zone_yamls = {zone: render_zone_yaml(zone, recs) for zone, recs in by_zone.items()}
    manifest = render_secret_manifest(zone_yamls)

    # Write plaintext to a temp file in the same dir (sops respects path-based
    # rules in .sops.yaml), then encrypt and atomically replace the target.
    target_dir = OUT_PATH.parent
    fd, tmp = tempfile.mkstemp(suffix=".yaml", prefix=".pull-", dir=target_dir)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(manifest)
        # Rename to the real path so .sops.yaml path rules apply.
        os.replace(tmp, OUT_PATH)
        sops_encrypt_in_place(OUT_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise

    totals = {z: len(r) for z, r in by_zone.items()}
    print(f"wrote {OUT_PATH} (encrypted): {totals}")


if __name__ == "__main__":
    main()
