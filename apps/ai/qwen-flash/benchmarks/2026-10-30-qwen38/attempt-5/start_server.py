import json
import subprocess
import resource
from pathlib import Path

# Match the published Docker recipe's memlock/stack ulimits in Kubernetes.
resource.setrlimit(resource.RLIMIT_MEMLOCK, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
resource.setrlimit(resource.RLIMIT_STACK, (64 * 1024 * 1024, 64 * 1024 * 1024))

MODEL = '/hf/benchmark38/hub/models--nvidia--Qwen3.8-Flash-Next-FP4-Local/snapshots/b9ce18a6887e18c089fdea8509a33e63429a3300'
args = ['vllm', 'serve', MODEL,
        '--served-model-name', 'qwen3.8-flash-next-fp4-local',
        '--host', '0.0.0.0', '--port', '8000',
        '--tensor-parallel-size', '1', '--pipeline-parallel-size', '1',
        '--mamba-cache-mode', 'align', '--enable-prefix-caching',
        '--enable-chunked-prefill', '--dtype', 'bfloat16',
        '--kv-cache-dtype', 'fp8', '--quantization', 'modelopt_mixed',
        '--block-size', '16', '--load-format', 'b12x',
        '--max-model-len', '32768', '--max-num-seqs', '4',
        '--max-num-batched-tokens', '1024',
        '--language-model-only', '--mm-processor-cache-gb', '0',
        '--gdn-decode-kernel', 'b12x', '--linear-backend', 'b12x',
        '--moe-backend', 'b12x', '--no-enable-flashinfer-autotune',
        '--mm-encoder-tp-mode', 'data', '--reasoning-parser', 'qwen3',
        '--tool-call-parser', 'qwen3_xml', '--enable-auto-tool-choice',
        '--compilation-config', '{"pass_config":{"fuse_act_quant":true}}',
        '--gpu-memory-utilization', '0.65',
        '--kv-cache-memory-bytes', '1073741824']
Path('/tmp/server-args.json').write_text(json.dumps(args, indent=2))
run_dir = Path('/hf/benchmark38/runs')
run_dir.mkdir(parents=True, exist_ok=True)
Path('/tmp/server.log').symlink_to(run_dir/'server.log')
with open(run_dir/'server.log', 'w') as log:
    proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
Path('/tmp/server.pid').write_text(str(proc.pid))
print(proc.pid)
