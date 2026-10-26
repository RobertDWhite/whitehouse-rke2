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
import struct
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


def report_hash(data):
    """sha256-tree-v1, shared with the operator; timestamp is not content."""
    h = hashlib.sha256()
    def visit(v):
        if v is None:
            h.update(b'n')
        elif isinstance(v, bool):
            h.update(b't' if v else b'f')
        elif isinstance(v, (int, float)):
            h.update(b'd' + struct.pack('>d', float(v)))
        elif isinstance(v, str):
            raw = v.encode('utf-8')
            h.update(b's' + str(len(raw)).encode() + b':' + raw)
        elif isinstance(v, list):
            h.update(b'[')
            for item in v:
                visit(item)
            h.update(b']')
        elif isinstance(v, dict):
            h.update(b'{')
            for key in sorted(k for k in v if v[k] is not None):
                visit(key)
                visit(v[key])
            h.update(b'}')
        else:
            raise ValueError('Unsupported report value')
    visit(dict(data, updateTimestamp=None))
    return h.hexdigest()


def complete_reports(reports):
    """Exclude parts and incomplete/corrupt manifests from CRD coverage."""
    reports = list(reports)
    prefix = 'trivy-operator.aquasecurity.github.io/report-'
    def annotations(r):
        return r.get('metadata', {}).get('annotations', {})
    def annotation(r, field):
        return annotations(r).get(prefix + field, '')
    def key(r):
        m = r.get('metadata', {})
        return m.get('namespace', ''), m.get('name', '')
    def summary(vulnerabilities):
        result = {s + 'Count': 0 for s in ('critical', 'high', 'medium', 'low', 'unknown', 'none')}
        for v in vulnerabilities:
            severity = v['severity'].lower()
            name = (severity if severity in ('critical', 'high', 'medium', 'low') else 'unknown') + 'Count'
            result[name] += 1
        return result
    def header(data):
        return {k: v for k, v in data.items() if k not in ('vulnerabilities', 'summary', 'updateTimestamp')}
    by_name = {key(r): r for r in reports}
    orphan_roots = {(key(r)[0], annotation(r, 'root')) for r in reports
                    if annotation(r, 'mode') == 'part' and
                    (key(r)[0], annotation(r, 'root')) not in by_name}
    incomplete = len(orphan_roots)
    complete = []
    for r in reports:
        mode = annotation(r, 'mode')
        if not mode:
            complete.append(r)
            continue
        if mode == 'part':
            continue
        try:
            if mode != 'manifest' or annotation(r, 'hash-format') != 'sha256-tree-v1':
                raise ValueError('Unknown partition protocol')
            names = json.loads(annotation(r, 'parts'))
            if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names) or len(set(names)) != len(names):
                raise ValueError('Invalid part references')
            data = r['report']
            findings = []
            generation = annotation(r, 'generation')
            if not generation or data.get('vulnerabilities'):
                raise ValueError('Invalid manifest')
            for index, name in enumerate(names):
                part = by_name[(key(r)[0], name)]
                pd = part['report']
                if (annotation(part, 'mode') != 'part' or
                    annotation(part, 'hash-format') != 'sha256-tree-v1' or
                    annotation(part, 'root') != key(r)[1] or
                    annotation(part, 'generation') != generation or
                    annotation(part, 'part-index') != str(index) or
                    header(pd) != header(data) or summary(pd['vulnerabilities']) != pd['summary']):
                    raise ValueError('Inconsistent partition')
                findings.extend(pd['vulnerabilities'])
            merged = dict(data, vulnerabilities=findings)
            if summary(findings) != data['summary'] or report_hash(merged) != generation:
                raise ValueError('Corrupt partition set')
            complete.append(dict(r, report=merged))
        except (KeyError, ValueError, TypeError, OverflowError):
            incomplete += 1
    return complete, incomplete


def crd_coverage(reports):
    # Ordinary findings are irrelevant to digest freshness. Consume them without
    # retaining the full cluster payload while keeping partition sets for checks.
    known = {}
    partitioned = []
    def remember(data):
        value = data.get('artifact', {}).get('digest')
        if value:
            known[value] = max(known.get(value, 0), timestamp(data.get('updateTimestamp')))
    for report in reports:
        mode = report.get('metadata', {}).get('annotations', {}).get(
            'trivy-operator.aquasecurity.github.io/report-mode', '')
        if mode:
            partitioned.append(report)
        else:
            remember(report['report'])
    complete, incomplete = complete_reports(partitioned)
    for report in complete:
        remember(report['report'])
    return known, incomplete


def coverage():
    now = time.time()
    wanted, missing = inventory(items('/api/v1/pods'))
    known, incomplete = crd_coverage(items('/apis/aquasecurity.github.io/v1alpha1/vulnerabilityreports'))
    crd_known = dict(known)
    archive_known = {}
    archived = list(receipts())
    latest = {}
    for receipt in archived:
        # A single report may list the same image under several registries.
        # Use one stable digest tuple, avoiding duplicated package counts.
        key = tuple(receipt['digests'])
        if key and receipt['scan_completed'] > latest.get(key, {}).get('scan_completed', 0):
            latest[key] = receipt
        for value in receipt['digests']:
            archive_known[value] = max(archive_known.get(value, 0), receipt['scan_completed'])
            known[value] = max(known.get(value, 0), receipt['scan_completed'])
    metrics = {'trivy_archive_crd_incomplete_sets': incomplete, 'trivy_archive_receipts': len(archived), 'trivy_inventory_missing_image_ids': missing}
    for severity in ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'UNKNOWN']:
        metrics[f'trivy_archive_latest_findings{{severity="{severity}"}}'] = sum(
            receipt['severity_counts'].get(severity, 0) for receipt in latest.values())
    for kind, values in wanted.items():
        # Combined evidence is useful, but must not hide failed CRD ingestion.
        metrics[f'trivy_inventory_crd_unmatched_digests{{kind="{kind}"}}'] = len(values - crd_known.keys())
        metrics[f'trivy_inventory_crd_stale_digests{{kind="{kind}"}}'] = sum(
            now - crd_known[v] > FRESH for v in values if v in crd_known)
        metrics[f'trivy_inventory_fresh_archive_without_fresh_crd{{kind="{kind}"}}'] = sum(
            v in archive_known and now - archive_known[v] <= FRESH and
            (v not in crd_known or now - crd_known[v] > FRESH) for v in values)
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
