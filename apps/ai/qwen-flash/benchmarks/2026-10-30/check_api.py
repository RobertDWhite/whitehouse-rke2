import argparse,json,urllib.request,time
from pathlib import Path
parser=argparse.ArgumentParser()
parser.add_argument('--base-url',default='http://127.0.0.1:18036/v1')
parser.add_argument('--output',type=Path,default=Path(__file__).with_name('api-checks.json'))
args=parser.parse_args()
base=args.base_url.rstrip('/')
def call(body):
 body={'model':'qwen3.6-35b-a3b','max_tokens':256,'temperature':0,'chat_template_kwargs':{'enable_thinking':False},**body}
 t=time.perf_counter(); req=urllib.request.Request(base+'/chat/completions',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=300) as r: d=json.load(r)
 return d,time.perf_counter()-t
results={}
d,elapsed=call({'messages':[{'role':'user','content':'Return only this JSON object with the computed integer values: {"six_times_seven": <6*7>, "minutes_in_two_hours": <2*60>}'}]})
s=d['choices'][0]['message']['content']; parsed=json.loads(s); assert parsed=={'six_times_seven':42,'minutes_in_two_hours':120},s
results['json_arithmetic']={'passed':True,'seconds':elapsed,'response':d}
d,elapsed=call({'messages':[{'role':'user','content':'Use get_node_status to check the node named spark. Do not answer until you have called the tool.'}],'tools':[{'type':'function','function':{'name':'get_node_status','description':'Get the Kubernetes status of one named node.','parameters':{'type':'object','properties':{'node':{'type':'string'}},'required':['node'],'additionalProperties':False}}}],'tool_choice':'auto'})
calls=d['choices'][0]['message'].get('tool_calls',[]); assert len(calls)==1,d
assert calls[0]['function']['name']=='get_node_status',d
assert json.loads(calls[0]['function']['arguments'])=={'node':'spark'},d
results['tool_call']={'passed':True,'seconds':elapsed,'response':d}
lines=[f'Inventory row {i}: service service-{i}, replicas 2, environment staging, owner platform.' for i in range(500)]
lines[237]='Inventory row 237: service northstar, replicas 3, environment production, owner inference.'
d,elapsed=call({'messages':[{'role':'user','content':'Read the inventory and return only the service name, replicas, environment and owner for row 237.\n'+'\n'.join(lines)}]})
s=d['choices'][0]['message']['content']; assert all(x in s.lower() for x in ['northstar','3','production','inference']),s
results['context_recall']={'passed':True,'seconds':elapsed,'response':d}
args.output.write_text(json.dumps(results,indent=2))
print(json.dumps({k:{'passed':v['passed'],'seconds':v['seconds'],'usage':v['response']['usage']} for k,v in results.items()},indent=2))
