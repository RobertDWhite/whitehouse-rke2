#!/usr/bin/env python3
"""Create an exact-match, expiring Alertmanager maintenance silence (dry-run default)."""
import argparse
import datetime as dt
import json
import urllib.request

# No telemetry gaps, anonymous access or broad security=true silence.
ALLOWED = {'SecurityKubeExec', 'SecurityKubeRBACChange', 'SecurityAccountChange',
           'SecurityFalcoHighPriority', 'SecurityFalcoWarningBurst'}
SCOPES = {'user', 'namespace', 'k8s_ns_name', 'host', 'hostname', 'rule'}


def payload(alert, scopes, minutes, owner, reason, now=None):
    if alert not in ALLOWED:
        raise ValueError('Alert is not an approved maintenance event rule')
    if not 1 <= minutes <= 240:
        raise ValueError('Maintenance must expire in 1–240 minutes')
    if not owner.strip() or not reason.strip():
        raise ValueError('Owner and reason are required')
    pairs = dict(s.split('=', 1) for s in scopes)
    if not pairs or any(k not in SCOPES or not v.strip() for k,v in pairs.items()):
        raise ValueError('At least one nonempty exact scope is required')
    if alert == 'SecurityKubeExec' and not {'user','namespace'} <= pairs.keys():
        raise ValueError('Exec maintenance requires both user and namespace')
    if alert.startswith('SecurityFalco') and not {'hostname','rule'} <= pairs.keys():
        raise ValueError('Runtime maintenance requires both hostname and rule')
    now = now or dt.datetime.now(dt.timezone.utc)
    return dict(matchers=[dict(name=k,value=v,isRegex=False,isEqual=True) for k,v in [('alertname',alert),*pairs.items()]],startsAt=now.isoformat(),endsAt=(now+dt.timedelta(minutes=minutes)).isoformat(),createdBy=owner,comment=reason)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url',default='http://127.0.0.1:19093',help='Use a local kubectl port-forward to Alertmanager')
    p.add_argument('--alert',required=True,choices=sorted(ALLOWED))
    p.add_argument('--scope',action='append',required=True,help='Exact label=value; repeat for narrower scope')
    p.add_argument('--minutes',type=int,default=60)
    p.add_argument('--owner',required=True);p.add_argument('--reason',required=True)
    p.add_argument('--apply',action='store_true')
    a=p.parse_args()
    try: data=payload(a.alert,a.scope,a.minutes,a.owner,a.reason)
    except ValueError as e: p.error(str(e))
    if not a.apply:
        print(json.dumps(data,indent=2));return
    req=urllib.request.Request(a.url.rstrip('/')+'/api/v2/silences',data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=15) as r: print(r.read().decode())

if __name__=='__main__': main()
