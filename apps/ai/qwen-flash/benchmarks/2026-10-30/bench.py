import argparse, concurrent.futures, json, time, urllib.request
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--port',type=int,default=18036); p.add_argument('--label',required=True); p.add_argument('--concurrency',type=int,default=1); p.add_argument('--output',required=True); p.add_argument('--context-rows',type=int,default=0); a=p.parse_args()
BASE=f'http://127.0.0.1:{a.port}/v1'
PROMPTS=[
('coding','Write a Python implementation of an LRU cache with get and put methods. Include type annotations, explain the invariants, and provide tests for capacity limits, replacement, and recently accessed items. Write a complete response of about 600 words.'),
('operations','Explain how Kubernetes resource requests, limits, readiness probes, and graceful shutdown interact during a rolling deployment of a stateful inference service. Include a concrete example and troubleshooting procedure. Write about 600 words.'),
('reasoning','Design a fair benchmark for comparing two local language model inference servers used for coding assistants. Explain how to separate input processing, output generation, concurrency, caching, and answer quality. Include reproducible measurement steps and common confounders. Write about 600 words.')]
if a.context_rows:
 context='Reference inventory for this exercise:\n'+'\n'.join(f'Inventory row {i}: service service-{i}, replicas 2, environment staging, owner platform.' for i in range(a.context_rows))+'\n\nTask:\n'
 PROMPTS=[(name,context+prompt) for name,prompt in PROMPTS]
def req(item):
 name,prompt=item; body={'model':'qwen3.6-35b-a3b','messages':[{'role':'user','content':prompt}], 'max_tokens':512,'temperature':0,'seed':42,'chat_template_kwargs':{'enable_thinking':False},'stream':True,'stream_options':{'include_usage':True}}
 t0=time.perf_counter(); first=None; last=None; usage={}; output=''; finish=None
 r=urllib.request.Request(BASE+'/chat/completions',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(r,timeout=300) as response:
  for line in response:
   if not line.startswith(b'data: '): continue
   raw=line[6:].strip()
   if raw==b'[DONE]': break
   d=json.loads(raw)
   if d.get('usage'): usage=d['usage']
   for c in d.get('choices',[]):
    delta=c.get('delta',{}); s=(delta.get('reasoning_content') or delta.get('reasoning') or '')+(delta.get('content') or '')
    if s:
     now=time.perf_counter(); first=first or now; last=now; output+=s
    if c.get('finish_reason'): finish=c['finish_reason']
 end=time.perf_counter(); tokens=usage.get('completion_tokens',0)
 return {'prompt':name,'label':a.label,'concurrency':a.concurrency,'prompt_tokens':usage.get('prompt_tokens'),'output_tokens':tokens,'ttft_seconds':None if first is None else first-t0,'elapsed_seconds':end-t0,'decode_tokens_per_second':None if not first or last==first else (tokens-1)/(last-first),'end_to_end_tokens_per_second':tokens/(end-t0),'finish_reason':finish,'response':output}
# Separate warmup from measured requests; do not count startup/compilation as decode.
warmup=req(('warmup','Write a short explanation of a binary search tree and its operations.'))
start=time.perf_counter()
if a.concurrency==1: results=[req(x) for x in PROMPTS]
else:
 items=[PROMPTS[i%len(PROMPTS)] for i in range(a.concurrency)]
 with concurrent.futures.ThreadPoolExecutor(max_workers=a.concurrency) as pool: results=list(pool.map(req,items))
elapsed=time.perf_counter()-start
report={'label':a.label,'concurrency':a.concurrency,'warmup':warmup,'results':results,'wall_seconds':elapsed,'aggregate_end_to_end_tokens_per_second':sum(x['output_tokens'] for x in results)/elapsed}
Path(a.output).write_text(json.dumps(report,indent=2))
print(json.dumps({k:v for k,v in report.items() if k not in ['warmup','results']},indent=2))
for r in results: print(json.dumps({k:v for k,v in r.items() if k!='response'}))
