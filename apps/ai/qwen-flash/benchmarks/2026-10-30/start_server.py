import json,sys,subprocess
mode=sys.argv[1]
args=['vllm','serve','/hf/benchmark/hub/models--nvidia--Qwen3.6-35B-A3B-NVFP4/snapshots/1355db6a052410cfd62085d94b58866fd0f2c3c5','--served-model-name','qwen3.6-35b-a3b','--host','0.0.0.0','--port','8000','--kv-cache-dtype','fp8','--attention-backend','flashinfer','--moe-backend','marlin','--gpu-memory-utilization','0.4','--max-model-len','131072','--max-num-seqs','4','--max-num-batched-tokens','8192','--enable-chunked-prefill','--async-scheduling','--enable-prefix-caching','--reasoning-parser','qwen3','--tool-call-parser','qwen3_xml','--enable-auto-tool-choice']
if mode=='mtp': args+=['--speculative-config',json.dumps({'method':'mtp','num_speculative_tokens':3,'moe_backend':'triton'})]
with open('/tmp/server-'+mode+'.log','w') as f:
 proc=subprocess.Popen(args,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
open('/tmp/server.pid','w').write(str(proc.pid))
open('/tmp/server-args-'+mode+'.json','w').write(json.dumps(args,indent=2))
print(proc.pid)
