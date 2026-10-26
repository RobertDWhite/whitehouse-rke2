#!/usr/bin/env python3
"""Read-only snapshot. Outputs selected metadata, never Secret objects or pod env."""
import collections
import datetime
import json
import importlib.util
from pathlib import Path
import subprocess


# Reuse the production archive reader so partial sets never count as coverage.
_reader_spec = importlib.util.spec_from_file_location(
    "trivy_archive_reader", Path(__file__).resolve().parents[1] / "trivy-archive" / "archiver.py")
_reader = importlib.util.module_from_spec(_reader_spec)
_reader_spec.loader.exec_module(_reader)


def resources(kind):
    return json.loads(subprocess.check_output(
        ['kubectl', 'get', kind, '--all-namespaces', '-o', 'json', '--request-timeout=30s']
    ))['items']


def snapshot():
    pods = [p for p in resources('pods') if p['status'].get('phase') == 'Running']
    report_objects = resources('vulnerabilityreports')
    reports, incomplete_sets = _reader.complete_reports(report_objects)
    policies = resources('networkpolicies')
    images = collections.defaultdict(set)
    for pod in pods:
        ns = pod['metadata']['namespace']
        for c in pod['status'].get('containerStatuses', []):
            digest = c.get('imageID', '').split('@')[-1]
            if digest.startswith('sha256:'):
                images[digest].add(ns)
    # Select newest report per immutable digest; do not count repeated ReplicaSets
    # or CronJobs as distinct vulnerable images. Index-vs-platform digest differences
    # can cause unmatched images; unmatched is not proof no scan ever ran.
    by_digest = {}
    for report in sorted(reports, key=lambda r: r['report'].get('updateTimestamp', '')):
        digest = report['report'].get('artifact', {}).get('digest')
        if digest in images:
            by_digest[digest] = report['report']
    findings = []
    for digest, report in by_digest.items():
        critical = [v for v in report.get('vulnerabilities', []) if v['severity'] == 'CRITICAL']
        findings.append({
            'digest': digest, 'namespaces': sorted(images[digest]),
            'artifact': report.get('artifact'), 'summary': report['summary'],
            'updated': report.get('updateTimestamp'),
            'critical_ids': sorted({v['vulnerabilityID'] for v in critical}),
        })
    findings.sort(key=lambda f: (f['summary'].get('criticalCount', 0), f['summary'].get('highCount', 0)), reverse=True)
    host_access = collections.Counter()
    for p in pods:
        s = p['spec']
        if any(s.get(k) for k in ['hostNetwork', 'hostPID', 'hostIPC']) or any(
            c.get('securityContext', {}).get('privileged')
            for c in s.get('containers', []) + s.get('initContainers', [])
        ) or any('hostPath' in v for v in s.get('volumes', [])):
            host_access[p['metadata']['namespace']] += 1
    external = []
    for s in resources('services'):
        if s['spec']['type'] in ['LoadBalancer', 'NodePort']:
            external.append({'namespace': s['metadata']['namespace'], 'name': s['metadata']['name'],
                             'type': s['spec']['type'], 'ports': [p['port'] for p in s['spec']['ports']]})
    admins = [{'binding': b['metadata']['name'], 'subjects': b.get('subjects', [])}
              for b in resources('clusterrolebindings') if b['roleRef']['name'] == 'cluster-admin']
    return {
        'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'running_pods': len(pods), 'running_container_digests': len(images),
        'vulnerability_reports': len(reports),
        'vulnerability_report_objects': len(report_objects),
        'incomplete_report_sets': incomplete_sets,
        'matched_running_digests': len(by_digest),
        'unmatched_running_digests': sorted(set(images) - set(by_digest)),
        'running_namespaces_without_networkpolicy': sorted(
            {p['metadata']['namespace'] for p in pods} - {p['metadata']['namespace'] for p in policies}),
        'pods_with_host_access_by_namespace': dict(sorted(host_access.items())),
        'external_service_types': external, 'cluster_admin_bindings': admins,
        'current_image_findings': findings,
        'limitations': [
            'Report digest matching is a lower bound; manifest-list and platform digests can differ.',
            'Running main-container image statuses only; init and ephemeral containers are not coverage denominators.',
            'Scanner findings do not prove exploitability. Unfixed and low severity findings may be filtered by scanner settings.',
            'NetworkPolicy presence does not prove effective isolation; rules are additive.',
            'LoadBalancer/NodePort does not establish public Internet reachability.',
            'Exact cluster-admin bindings omit equivalent wildcard permissions and indirect escalation paths.',
        ],
    }


if __name__ == '__main__':
    print(json.dumps(snapshot(), indent=2))
