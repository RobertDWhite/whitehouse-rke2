#!/usr/bin/env python3
"""Exercise actual Loki parsing with synthetic, NON-alerting source records.
Usage: python3 tests/verify_loki_policy.py http://LOKI:3100
Source=soc-policy-test is deliberately outside production syscall selectors.
"""
import copy
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import runtime_policy as policy

base=sys.argv[1].rstrip('/')
run=str(time.time_ns())
normal='12:00:00: Critical SOC policy test | evt_type=execve user=root process=rsync proc_exepath=/usr/bin/rsync parent=sh command=rsync terminal=0 container_id=test container_name=rsync container_image_repository=docker.io/library/alpine container_image_tag=3.22 k8s_pod_name=immich-upload-to-synology-sync-123-abc k8s_ns_name=immich'
cases=[('expected',normal,'immich','expected'),('other-namespace',normal.replace('k8s_ns_name=immich','k8s_ns_name=other'),'other','actionable'),('unexpected-executable',normal.replace('proc_exepath=/usr/bin/rsync','proc_exepath=/tmp/payload'),'immich','actionable'),('malformed','malformed raw event','immich','actionable'),('command-spoof',normal.replace('process=rsync proc_exepath=/usr/bin/rsync parent=sh command=rsync','process=curl proc_exepath=/usr/bin/curl parent=bash command=process=rsync proc_exepath=/usr/bin/rsync parent=sh command=rsync'),'immich','actionable')]
streams=[]
for name,line,namespace,expected in cases:
 streams.append(dict(stream=dict(source='soc-policy-test',test_run=run,test_case=name,priority='Critical',rule='Drop and execute new binary in container',k8s_ns_name=namespace,k8s_pod_name='immich-upload-to-synology-sync-123-abc'),values=[[run,line]]))
req=urllib.request.Request(base+'/loki/api/v1/push',data=json.dumps(dict(streams=streams)).encode(),headers={'Content-Type':'application/json'})
with urllib.request.urlopen(req,timeout=20) as r: assert r.status==204

def query():
 q='{source="soc-policy-test",test_run="'+run+'"}'+policy.pipeline()
 url=base+'/loki/api/v1/query_range?'+urllib.parse.urlencode(dict(query=q,start=int(run)-1,end=time.time_ns(),limit=100))
 with urllib.request.urlopen(url,timeout=30) as r: return json.load(r)['data']['result']
result=query();actual={s['stream']['test_case']:s['stream']['disposition'] for s in result}
assert actual=={name:expected for name,_,_,expected in cases},actual
# Expiry is checked by the actual Loki template, not only Python's evaluator.
for e in policy.POLICY: e['expires']='2020-01-01T00:00:00Z'
result=query();assert all(s['stream']['disposition']=='actionable' for s in result)
print('PASS: live Loki expected match, namespace/executable boundaries, malformed input, command spoofing and expired policy; no production alert source used')
