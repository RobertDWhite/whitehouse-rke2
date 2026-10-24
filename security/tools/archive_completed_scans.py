#!/usr/bin/env python3
"""Archive completed Trivy jobs outside Git. Never print report/log contents.

This is a manual recovery tool, not a replacement for continuous report storage.
Run before job cleanup; a nonzero exit means the archive is incomplete.
"""
import argparse
import base64
import bz2
import collections
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess


def kubectl(*args):
    result = subprocess.run(
        ['kubectl', '--request-timeout=30s', *args], capture_output=True, timeout=60)
    if result.returncode:
        # Kubernetes errors/logs may contain sensitive values.
        raise RuntimeError('kubectl command failed; raw stderr withheld')
    return result.stdout


def decode_report(raw):
    # Trivy operator 0.28.0 emits base64(bzip2(JSON)) to scan-container logs.
    encoded = b''.join(raw.split())
    report = json.loads(bz2.decompress(base64.b64decode(encoded, validate=True)))
    # Trivy omits Results entirely for an image with no reportable targets.
    if not isinstance(report, dict) or not {'SchemaVersion', 'ArtifactName'} <= report.keys():
        raise ValueError('Not a Trivy report')
    return report


def summary(report):
    findings = [v for r in report.get('Results') or []
                for v in r.get('Vulnerabilities') or []]
    metadata = report.get('Metadata', {})
    return {
        'artifact_name': report.get('ArtifactName'),
        'artifact_type': report.get('ArtifactType'),
        'image_id': metadata.get('ImageID'),
        'repo_digests': metadata.get('RepoDigests', []),
        'created_at': report.get('CreatedAt'),
        'severity_counts': dict(collections.Counter(v.get('Severity', 'UNKNOWN') for v in findings)),
        'unique_vulnerability_ids': len({v['VulnerabilityID'] for v in findings}),
    }


def write_private(path, data):
    with path.open('xb') as stream:
        os.chmod(path, 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def archive(root):
    root = root.expanduser().resolve()
    repository = Path(__file__).resolve().parents[2]
    if root == repository or repository in root.parents:
        raise ValueError('Archive directory must be outside the repository')
    os.umask(0o077)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.stat().st_mode & 0o077:
        raise ValueError('Archive root must have owner-only permissions')
    output = root / datetime.datetime.now(datetime.timezone.utc).strftime('trivy-%Y%m%dT%H%M%S.%fZ')
    output.mkdir(mode=0o700)
    jobs = json.loads(kubectl('get', 'jobs', '-n', 'trivy-system', '-o', 'json'))['items']
    receipt = {'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'namespace': 'trivy-system', 'containers': [], 'errors': []}
    for job in jobs:
        if not job['status'].get('completionTime'):
            continue
        name = job['metadata']['name']
        if not name.startswith('scan-vulnerabilityreport-'):
            continue
        try:
            pods = json.loads(kubectl('get', 'pods', '-n', 'trivy-system',
                                     '-l', 'job-name=' + name, '-o', 'json'))['items']
            if not pods:
                raise RuntimeError('Completed job has no retained pod')
            for pod in pods:
                for container in pod['spec']['containers']:
                    raw = kubectl('logs', '-n', 'trivy-system', pod['metadata']['name'],
                                  '-c', container['name'])
                    filename = pod['metadata']['name'] + '--' + container['name'] + '.log'
                    write_private(output / filename, raw)
                    entry = {'job': name, 'job_uid': job['metadata']['uid'],
                             'completed_at': job['status']['completionTime'],
                             'pod': pod['metadata']['name'], 'container': container['name'],
                             'file': filename, 'bytes': len(raw),
                             'sha256': hashlib.sha256(raw).hexdigest()}
                    receipt['containers'].append(entry)
                    try:
                        report = decode_report(raw)
                        entry['summary'] = summary(report)
                        # Keep the complete report (including any secret findings)
                        # only in the restricted archive, never in stdout or Git.
                        write_private(output / (filename + '.json'), json.dumps(report).encode())
                        entry['decoded_file'] = filename + '.json'
                    except (ValueError, OSError, EOFError, KeyError, TypeError):
                        entry['decode_failed'] = True
                        receipt['errors'].append({'job': name, 'container': container['name'],
                                                  'reason': 'report decode failed; raw log retained'})
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            receipt['errors'].append({'job': name, 'reason': type(error).__name__})
    write_private(output / 'manifest.json', json.dumps(receipt, indent=2).encode())
    print(f'Archive: {output}')
    print(f'Container logs: {len(receipt["containers"])}; failures: {len(receipt["errors"])}')
    return 1 if receipt['errors'] else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True,
                        help='Owner-only archive root outside the repository')
    args = parser.parse_args()
    raise SystemExit(archive(args.directory))
