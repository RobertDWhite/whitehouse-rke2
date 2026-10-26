#!/usr/bin/env python3
"""Exercise note persistence only in a digest-pinned, isolated restore candidate."""
import argparse
import json
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--pod', required=True)
parser.add_argument('--image', required=True)
args = parser.parse_args()
if '@sha256:' not in args.image:
    raise SystemExit('An immutable candidate image is required')
pod = json.loads(subprocess.check_output([
    'kubectl', 'get', 'pod', args.pod, '-n', 'backoffice', '-o', 'json'
]))
if pod['metadata'].get('labels', {}).get('security-check') != 'backoffice-api':
    raise SystemExit('Refusing to write test data outside the isolated API candidate')
if pod['spec'].get('automountServiceAccountToken') is not False:
    raise SystemExit('Candidate must not mount a Kubernetes API token')
container = next(c for c in pod['spec']['containers'] if c['name'] == 'api')
status = next(c for c in pod['status'].get('containerStatuses', []) if c['name'] == 'api')
if not status.get('ready') or status.get('imageID', '').split('@')[-1] != args.image.split('@')[-1]:
    raise SystemExit('Candidate runtime digest is not ready or does not match')
env = {v['name']: v.get('value') for v in container.get('env', [])}
if container['image'] != args.image or env.get('POSTGRES_HOST') != 'backoffice-restore-check':
    raise SystemExit('Candidate image or restored-database destination does not match')
code = r'''
import datetime, json, uuid, urllib.request
base = 'http://127.0.0.1:8000'
marker = 'security-validation-' + uuid.uuid4().hex
payload = {'namespace': marker, 'app_name': 'restore-test', 'note': 'Synthetic recovery validation'}
def request(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as response:
        raw = response.read()
        return response.status, json.loads(raw) if raw else None
note_id = None
try:
    assert request('GET', '/healthz')[0] == 200
    status, created = request('POST', '/api/notes', payload)
    assert status == 201
    note_id = created['id']
    assert all(created[k] == v for k, v in payload.items())
    for field in ('created_at', 'updated_at'):
        datetime.datetime.fromisoformat(created[field])
    status, rows = request('GET', '/api/notes?namespace=' + marker)
    assert status == 200 and len(rows) == 1 and rows[0]['id'] == note_id
    assert all(rows[0][k] == v for k, v in payload.items())
finally:
    # Use the unique namespace if creation succeeded but its response was interrupted.
    _, rows = request('GET', '/api/notes?namespace=' + marker)
    for row in rows:
        assert row['namespace'] == marker and row['app_name'] == 'restore-test'
        assert request('DELETE', '/api/notes/' + str(row['id']))[0] == 204
assert request('GET', '/api/notes?namespace=' + marker) == (200, [])
print('Restored-database health, note create/read/delete and cleanup passed')
'''
subprocess.run(['kubectl', 'exec', '-i', '-n', 'backoffice', args.pod,
                '-c', 'api', '--', 'python', '-'], input=code.encode(), check=True)
