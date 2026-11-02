"""Temporary Spark swap with unconditional cleanup. Stage weights and image first."""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODEL = 'qwen3.8-flash-next-fp4-local'
PORT = 18038
BASE = f'http://127.0.0.1:{PORT}/v1'
POD = 'spark38-benchmark'

def run(*args, check=True, **kwargs):
    return subprocess.run(args, check=check, text=True, **kwargs)

def k(*args, **kwargs):
    return run('kubectl', *args, **kwargs)

def save_logs():
    for container in ('vllm', 'mem-watchdog'):
        if container == 'vllm':
            r = k('exec', '-n', 'qwen-flash', POD, '-c', container, '--',
                  'cat', '/tmp/server.log', check=False, capture_output=True)
        else:
            r = k('logs', '-n', 'qwen-flash', POD, '-c', container,
                  check=False, capture_output=True)
        (HERE / f'{container}.log').write_text(r.stdout + r.stderr)

app = json.loads(k('get', 'application', '-n', 'argocd', 'qwen-flash',
                   '-o', 'json', capture_output=True).stdout)
assert not app['metadata'].get('annotations', {}).get('argocd.argoproj.io/skip-reconcile')
deployment = json.loads(k('get', 'deployment', '-n', 'qwen-flash', 'qwen-flash',
                          '-o', 'json', capture_output=True).stdout)
replicas = deployment['spec']['replicas']
forward = None
outcomes = {}
try:
    k('annotate', 'application', '-n', 'argocd', 'qwen-flash',
      'argocd.argoproj.io/skip-reconcile=true', '--overwrite')
    k('scale', 'deployment/qwen-flash', '-n', 'qwen-flash', '--replicas=0')
    k('wait', '-n', 'qwen-flash', '--for=delete', 'pod', '-l', 'app=qwen-flash', '--timeout=120s')
    k('apply', '-f', str(HERE / 'benchmark-pod.json'))
    k('wait', '-n', 'qwen-flash', '--for=condition=Ready', 'pod/'+POD, '--timeout=180s')
    k('exec', '-i', '-n', 'qwen-flash', POD, '-c', 'vllm', '--', 'python3', '-',
      input=(HERE / 'start_server.py').read_text())
    with open(HERE / 'port-forward.log', 'w') as log:
        forward = subprocess.Popen(['kubectl', 'port-forward', '-n', 'qwen-flash',
                    'pod/'+POD, f'{PORT}:8000'], stdout=log, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 1800
    started = time.monotonic()
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(BASE+'/models', timeout=3) as r:
                models = json.load(r)
            assert models['data'][0]['id'] == MODEL
            (HERE / 'models.json').write_text(json.dumps(models, indent=2))
            break
        except Exception:
            if forward.poll() is not None:
                with open(HERE/'port-forward.log', 'a') as log_file:
                    forward = subprocess.Popen(['kubectl', 'port-forward', '-n', 'qwen-flash',
                                'pod/'+POD, f'{PORT}:8000'], stdout=log_file, stderr=subprocess.STDOUT)
                print('Restarted interrupted port-forward', flush=True)
            response = k('get', 'pod', '-n', 'qwen-flash', POD, '-o', 'json',
                         capture_output=True, check=False)
            if response.returncode:
                print('Kubernetes status transport unavailable; retrying', flush=True)
                time.sleep(20)
                continue
            status = json.loads(response.stdout)
            states = status['status'].get('containerStatuses', [])
            if any(s['state'].get('terminated') for s in states):
                raise RuntimeError('Benchmark container stopped; see watchdog log')
            log = k('exec', '-n', 'qwen-flash', POD, '-c', 'vllm', '--',
                    'tail', '-5', '/tmp/server.log', capture_output=True, check=False).stdout
            print(f'Startup {time.monotonic()-started:.0f}s: {log}', flush=True)
            alive = k('exec', '-n', 'qwen-flash', POD, '-c', 'vllm', '--',
                      'sh', '-c', 'pid=$(cat /tmp/server.pid); if test -r /proc/$pid/stat && test "$(awk \'{print $3}\' /proc/$pid/stat)" != Z; then echo running; else echo exited; fi',
                      capture_output=True, check=False)
            if alive.returncode == 0 and alive.stdout.strip() == 'exited':
                raise RuntimeError('Server process exited; see vllm.log')
            if alive.returncode:
                print('Kubelet exec transport unavailable; process state unknown', flush=True)
            if 'Traceback (most recent call last)' in log:
                raise RuntimeError('Server startup failed')
            time.sleep(20)
    else:
        raise TimeoutError('Server did not become ready within 30 minutes')
    tests = [
        ('single', 'bench.py', ['--port', str(PORT), '--model', MODEL, '--label', 'qwen38-no-mtp-heuristic-single', '--output', str(HERE/'qwen38-single.json')]),
        ('four', 'bench.py', ['--port', str(PORT), '--model', MODEL, '--label', 'qwen38-no-mtp-heuristic-four', '--concurrency', '4', '--output', str(HERE/'qwen38-four.json')]),
        ('long', 'bench.py', ['--port', str(PORT), '--model', MODEL, '--label', 'qwen38-no-mtp-heuristic-long', '--context-rows', '800', '--output', str(HERE/'qwen38-long.json')]),
        ('api', 'check_api.py', ['--base-url', BASE, '--model', MODEL, '--output', str(HERE/'qwen38-api.json')]),
        ('quality', 'quality.py', ['--base-url', BASE, '--model', MODEL, '--output', str(HERE/'qwen38-quality.json')]),
        ('quality-thinking', 'quality.py', ['--base-url', BASE, '--model', MODEL, '--thinking', '--output', str(HERE/'qwen38-quality-thinking.json')]),
        ('quality-thinking-8192', 'quality.py', ['--base-url', BASE, '--model', MODEL, '--thinking', '--max-tokens', '8192', '--output', str(HERE/'qwen38-quality-thinking-8192.json')]),
    ]
    for name, script, args in tests:
        print('TEST', name, flush=True)
        outcomes[name] = run(sys.executable, str(HERE/script), *args, check=False, timeout=1200).returncode
        (HERE/'outcomes.json').write_text(json.dumps(outcomes, indent=2))
    r = k('exec', '-n', 'qwen-flash', POD, '-c', 'vllm', '--', 'sh', '-c',
          'head -3 /proc/meminfo; nvidia-smi --query-gpu=temperature.gpu,utilization.gpu,clocks.current.graphics --format=csv,noheader', capture_output=True)
    (HERE/'resources.txt').write_text(r.stdout)
finally:
    try:
        save_logs()
    except Exception as error:
        print('Log capture failed:', error, flush=True)
    if forward:
        forward.terminate()
        forward.wait(timeout=10)
    k('delete', 'pod', '-n', 'qwen-flash', POD, '--ignore-not-found', '--wait=true', '--timeout=120s', check=False)
    k('scale', 'deployment/qwen-flash', '-n', 'qwen-flash', f'--replicas={replicas}')
    k('annotate', 'application', '-n', 'argocd', 'qwen-flash', 'argocd.argoproj.io/skip-reconcile-')
    k('annotate', 'application', '-n', 'argocd', 'qwen-flash', 'argocd.argoproj.io/refresh=normal', '--overwrite')
    print('Production configuration restored; checking readiness.', flush=True)
    k('rollout', 'status', 'deployment/qwen-flash', '-n', 'qwen-flash', '--timeout=600s')
    # The watchdog may kill the benchmark shell too; recover its durable log
    # from the shared model PVC after production is healthy.
    r = k('exec', '-n', 'qwen-flash', 'deployment/qwen-flash', '-c', 'vllm', '--',
          'cat', '/hf/benchmark38/runs/server.log', check=False, capture_output=True)
    if r.returncode == 0:
        (HERE/'vllm.log').write_text(r.stdout)
