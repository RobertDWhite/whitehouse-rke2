"""Small deterministic checks; not a general coding benchmark. Never executes model code."""
import argparse
import json
import time
import urllib.request
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--base-url', required=True)
p.add_argument('--model', required=True)
p.add_argument('--output', required=True)
p.add_argument('--thinking', action='store_true')
p.add_argument('--max-tokens', type=int, default=2048)
a = p.parse_args()
CASES = [
    ('closure', 'What does this Python print? Return JSON {"result": [...]} only.\nfs = [lambda: i for i in range(3)]\nprint([f() for f in fs])', {'result': [2, 2, 2]}),
    ('aliasing', 'What does this Python print? Return JSON {"result": [...]} only.\na = [[0]] * 3\na[0].append(1)\na[1] = [9]\nprint(a)', {'result': [[0, 1], [9], [0, 1]]}),
    ('lru', 'A capacity-2 LRU cache starts empty. Apply put(A,1), put(B,2), get(A), put(C,3), put(A,4), get(B), put(D,5). Return JSON only with final keys ordered least-to-most recently used and value of A: {"keys": [...], "A": number}.', {'keys': ['A', 'D'], 'A': 4}),
    ('binary_search', 'This Python lower_bound is wrong: while lo < hi: mid=(lo+hi)//2; if a[mid] < x: lo=mid; else: hi=mid. Initially lo=0, hi=len(a). Return JSON only identifying the replacement for lo=mid and the result for a=[1,2,2,4], x=2: {"replacement": "...", "result": number}. Use exactly "lo = mid + 1" for the replacement if appropriate.', {'replacement': 'lo = mid + 1', 'result': 1}),
    ('sql_null', 'Tables A(id) contain 1,2,3 and B(id) contain 2,NULL. Under standard SQL, return JSON only with row counts: {"not_in": number, "not_exists": number}. Query 1: SELECT * FROM A WHERE id NOT IN (SELECT id FROM B). Query 2: SELECT * FROM A WHERE NOT EXISTS (SELECT 1 FROM B WHERE B.id=A.id).', {'not_in': 0, 'not_exists': 2}),
    ('deployment', 'Kubernetes Deployment: replicas=1, maxSurge=1, maxUnavailable=0. The only eligible node has exactly one allocatable GPU, occupied by the existing pod. Each pod requests one GPU. Can a replacement schedule without freeing the old GPU? Does a failed readiness probe itself restart a container? Return JSON only: {"replacement_can_schedule": boolean, "readiness_restarts_container": boolean}.', {'replacement_can_schedule': False, 'readiness_restarts_container': False}),
    ('intervals', 'Merge overlapping half-open intervals, but do not merge intervals that only touch. Input: [[1,4],[4,5],[2,3],[8,10],[9,12],[5,6]]. Return JSON only: {"intervals": [...]}.', {'intervals': [[1, 4], [4, 5], [5, 6], [8, 12]]}),
    ('retry', 'An idempotent operation succeeds independently with probability 0.5 per attempt. Allow at most 3 attempts, stopping on success. Return JSON only: {"success_probability": number, "expected_attempts": number}.', {'success_probability': 0.875, 'expected_attempts': 1.75}),
]
results = []
for name, prompt, expected in CASES:
    body = {'model': a.model, 'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': a.max_tokens, 'temperature': 0, 'seed': 42,
            'chat_template_kwargs': {'enable_thinking': a.thinking}}
    req = urllib.request.Request(a.base_url.rstrip('/')+'/chat/completions',
          data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    t = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as r:
        response = json.load(r)
    content = response['choices'][0]['message'].get('content') or ''
    try:
        parsed = json.loads(content)
        passed = parsed == expected
    except (ValueError, TypeError):
        parsed, passed = None, False
    results.append({'case': name, 'passed': passed, 'expected': expected,
                    'seconds': time.perf_counter()-t, 'response': response})
    print(name, 'PASS' if passed else 'FAIL', flush=True)
    Path(a.output).write_text(json.dumps({'model': a.model, 'thinking': a.thinking, 'max_tokens': a.max_tokens,
                                        'results': results}, indent=2))
print(f'{sum(r["passed"] for r in results)}/{len(results)} passed')
