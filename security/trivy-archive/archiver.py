"""Retain completed Trivy logs on a PVC; serve aggregate coverage metrics only."""
import base64
import bz2
import datetime
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import ssl
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(os.getenv('ARCHIVE_DIR', '/archive'))
SA = Path('/var/run/secrets/kubernetes.io/serviceaccount')
RETENTION = 14 * 86400
FRESH = 48 * 3600
MAX_LOG = 32 * 1024 * 1024
METRICS = {'trivy_archive_last_success_timestamp_seconds': 0,
           'trivy_archive_errors': 0, 'trivy_archive_coverage_last_success_timestamp_seconds': 0}
LOCK = threading.Lock()


def timestamp(value):
    return datetime.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() if value else 0


def digest(value):
    value = value.rsplit('@', 1)[-1].removeprefix('docker-pullable://').removeprefix('containerd://')
    return value if value.startswith('sha256:') and len(value) == 71 else None


def api(path, limit=64 * 1024 * 1024):
    request = Request('https://kubernetes.default.svc' + path,
                      headers={'Authorization': 'Bearer ' + (SA / 'token').read_text().strip()})
    with urlopen(request, context=ssl.create_default_context(cafile=str(SA / 'ca.crt')), timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('API response exceeds memory bound')
    return data


def items(path, query=None):
    query = dict(query or {}, limit=100)
    while True:
        data = json.loads(api(path + '?' + urlencode(query)))
        yield from data['items']
        token = data.get('metadata', {}).get('continue')
        if not token:
            return
        query['continue'] = token


def parse(raw):
    decoder = bz2.BZ2Decompressor()
    data = decoder.decompress(base64.b64decode(b''.join(raw.split()), validate=True),
                              max_length=128 * 1024 * 1024)
    if not decoder.eof:
        raise ValueError('Decoded report exceeds memory bound')
    report = json.loads(data)
    if not {'SchemaVersion', 'ArtifactName'} <= report.keys():
        raise ValueError('Invalid report')
    return report


def atomic(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('wb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def store(job, pod, container, raw):
    directory = ROOT / job['metadata']['uid']
    directory.mkdir(mode=0o700, exist_ok=True)
    name = pod['metadata']['uid'] + '--' + container['name']
    # Raw output persists even if decoding fails. Receipts are written last.
    atomic(directory / (name + '.log.gz'), gzip.compress(raw))
    report = parse(raw)
    findings = [v for result in report.get('Results') or []
                for v in result.get('Vulnerabilities') or []]
    counts = {}
    for finding in findings:
        severity = finding.get('Severity', 'UNKNOWN')
        counts[severity] = counts.get(severity, 0) + 1
    metadata = report.get('Metadata', {})
    # RepoDigests are artifact digests. ImageID is a config digest, not a
    # platform/manifest digest, and must not count as coverage.
    digests = sorted({d for value in metadata.get('RepoDigests') or [] if (d := digest(value))})
    receipt = {'job': job['metadata']['name'], 'job_uid': job['metadata']['uid'],
               'pod_uid': pod['metadata']['uid'], 'container': container['name'],
               'scanner_image': container.get('image'),
               'scan_completed': timestamp(job['status']['completionTime']),
               'archived_at': time.time(), 'digests': digests,
               'severity_counts': counts, 'raw_sha256': hashlib.sha256(raw).hexdigest(),
               'raw_bytes': len(raw), 'file': name + '.log.gz'}
    atomic(directory / (name + '.json'), json.dumps(receipt).encode())
    return receipt


def archive_once():
    errors = 0
    for job in items('/apis/batch/v1/namespaces/trivy-system/jobs'):
        if not job['metadata']['name'].startswith('scan-vulnerabilityreport-') or not job['status'].get('completionTime'):
            continue
        try:
            pods = list(items('/api/v1/namespaces/trivy-system/pods',
                              {'labelSelector': 'job-name=' + job['metadata']['name']}))
            if not pods:
                raise ValueError('Missing scan pod')
            for pod in pods:
                for container in pod['spec']['containers']:
                    path = ROOT / job['metadata']['uid'] / (pod['metadata']['uid'] + '--' + container['name'] + '.json')
                    if path.exists():
                        continue
                    raw = api('/api/v1/namespaces/trivy-system/pods/' + pod['metadata']['name'] +
                              '/log?' + urlencode({'container': container['name']}), MAX_LOG)
                    store(job, pod, container, raw)
        except Exception:
            # Never log responses, reports, credentials, or secret findings.
            errors += 1
    return errors


def receipts():
    for path in ROOT.glob('*/*.json'):
        yield json.loads(path.read_text())


def inventory(pods):
    result = {'main': set(), 'init': set(), 'ephemeral': set()}
    missing = 0
    for pod in pods:
        if pod['status'].get('phase') != 'Running':
            continue
        for kind, spec_key, status_key in [('main', 'containers', 'containerStatuses'),
                                           ('init', 'initContainers', 'initContainerStatuses'),
                                           ('ephemeral', 'ephemeralContainers', 'ephemeralContainerStatuses')]:
            statuses = {c['name']: c for c in pod['status'].get(status_key, [])}
            for container in pod['spec'].get(spec_key, []):
                value = digest(statuses.get(container['name'], {}).get('imageID', ''))
                if value:
                    result[kind].add(value)
                else:
                    missing += 1
    return result, missing


def coverage():
    now = time.time()
    wanted, missing = inventory(items('/api/v1/pods'))
    known = {}
    for report in items('/apis/aquasecurity.github.io/v1alpha1/vulnerabilityreports'):
        data = report['report']
        value = data.get('artifact', {}).get('digest')
        if value:
            known[value] = max(known.get(value, 0), timestamp(data.get('updateTimestamp')))
    archived = list(receipts())
    latest = {}
    for receipt in archived:
        # A single report may list the same image under several registries.
        # Use one stable digest tuple, avoiding duplicated package counts.
        key = tuple(receipt['digests'])
        if key and receipt['scan_completed'] > latest.get(key, {}).get('scan_completed', 0):
            latest[key] = receipt
        for value in receipt['digests']:
            known[value] = max(known.get(value, 0), receipt['scan_completed'])
    metrics = {'trivy_archive_receipts': len(archived), 'trivy_inventory_missing_image_ids': missing}
    for severity in ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'UNKNOWN']:
        metrics[f'trivy_archive_latest_findings{{severity="{severity}"}}'] = sum(
            receipt['severity_counts'].get(severity, 0) for receipt in latest.values())
    for kind, values in wanted.items():
        metrics[f'trivy_inventory_digests{{kind="{kind}"}}'] = len(values)
        metrics[f'trivy_inventory_unmatched_digests{{kind="{kind}"}}'] = len(values - known.keys())
        metrics[f'trivy_inventory_stale_digests{{kind="{kind}"}}'] = sum(now - known[v] > FRESH for v in values if v in known)
    metrics['trivy_archive_coverage_last_success_timestamp_seconds'] = now
    return metrics


def prune():
    cutoff = time.time() - RETENTION
    for directory in ROOT.iterdir():
        if not directory.is_dir():
            continue
        # Age of newest file, including unsuccessful raw captures.
        newest = max((p.stat().st_mtime for p in directory.iterdir()), default=directory.stat().st_mtime)
        if newest < cutoff:
            shutil.rmtree(directory)


def worker():
    next_coverage = 0
    while True:
        try:
            errors = archive_once()
            prune()
            space = shutil.disk_usage(ROOT)
            values = {'trivy_archive_errors': errors,
                      'trivy_archive_free_bytes': space.free,
                      'trivy_archive_capacity_bytes': space.total}
            if not errors:
                values['trivy_archive_last_success_timestamp_seconds'] = time.time()
            if time.time() >= next_coverage:
                try:
                    values.update(coverage())
                    next_coverage = time.time() + 300
                except Exception:
                    print('Coverage refresh failed; previous metrics retained', flush=True)
            with LOCK:
                METRICS.update(values)
        except Exception:
            with LOCK:
                METRICS['trivy_archive_errors'] += 1
            print('Archive cycle failed; details withheld', flush=True)
        time.sleep(30)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ('/metrics', '/healthz'):
            self.send_error(404)
            return
        with LOCK:
            output = ''.join(f'{key} {value}\n' for key, value in sorted(METRICS.items())).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; version=0.0.4')
        self.end_headers()
        self.wfile.write(output)

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    os.umask(0o077)
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    threading.Thread(target=worker, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', 9100), Handler).serve_forever()
