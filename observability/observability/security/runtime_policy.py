#!/usr/bin/env python3
"""Generate the same fail-open classification pipeline for Loki alerts and Grafana.

Classification does not delete logs or change Falco severity. Unknown/malformed
records stay actionable (Error+) or unreviewed. Expiry uses event time, so old
records retain their classification and new activity stops matching on expiry.
"""
import argparse
import datetime as dt
import json
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
POLICY = json.loads((HERE / 'runtime-policy.json').read_text())


def pattern(value):
    return '^(' + value.replace('.', r'\.') + ')$'


def pipeline():
    # Parse the trusted Falco prefix and suffix, never command arguments. The
    # suffix is end-anchored so a command containing field-like text cannot
    # impersonate a workload. Namespace/pod are original Loki stream labels.
    parsers = [
        r'^[^|]+\| (?:.*? )?process=(?P<soc_process>\S+) proc_exepath=(?P<soc_exe>\S+) parent=(?P<soc_parent>\S+) command=',
        r' container_name=(?P<soc_container>\S*) container_image_repository=(?P<soc_image>\S*) container_image_tag=(?P<soc_image_tag>\S*) k8s_pod_name=\S+ k8s_ns_name=\S+$',
        r'^[^|]+\| file=(?P<soc_file>\S+) ',
        r'^[^|]+\| .*proc_sname=runc:\[1:CHILD\] gparent=runc:\[1:CHILD\] ',
    ]
    out = ' | regexp `' + parsers[0] + '` | regexp `' + parsers[1] + '` | regexp `' + parsers[2] + '`'
    out += ' | regexp `^[^|]+\\| .*proc_sname=(?P<soc_runtime_hook>runc:\\[1:CHILD\\]) gparent=runc:\\[1:CHILD\\] `'
    clauses = []
    for entry in POLICY:
        expiry = str(int(dt.datetime.fromisoformat(entry['expires'].replace('Z','+00:00')).timestamp()))
        terms = ['(eq .rule ' + json.dumps(entry['rule']) + ')', '(lt (unixEpoch (__timestamp__)) ' + json.dumps(expiry) + ')']
        for key, value in entry['fields'].items():
            label = key if key.startswith('k8s_') else 'soc_' + key
            terms.append('(ne .' + label + ' "") (eq (regexReplaceAll ' + json.dumps(pattern(value)) + ' .' + label + ' "") "")')
        clauses.append(('if' if not clauses else 'else if') + ' and ' + ' '.join(terms) + ' }}' + entry['id'])
    template = '{{ ' + '{{ '.join(clauses) + '{{ end }}'
    out += ' | label_format expected_reason=`' + template + '`'
    out += ' | label_format disposition=`{{ if ne .expected_reason "" }}expected{{ else if or (eq .priority "Emergency") (eq .priority "Alert") (eq .priority "Critical") (eq .priority "Error") }}actionable{{ else }}unreviewed{{ end }}`'
    return out


def classify(fields, rule, timestamp):
    """Reference evaluator for offline boundary tests; queries tested on Loki."""
    for entry in POLICY:
        expiry = dt.datetime.fromisoformat(entry['expires'].replace('Z','+00:00')).timestamp()
        if rule == entry['rule'] and timestamp < expiry and all(re.fullmatch(pattern(v), fields.get(k,'')) for k,v in entry['fields'].items()):
            return entry['id']
    return ''


def generate():
    pipe = pipeline()
    queries = {
        'SecurityFalcoHighPriority': 'sum by (hostname,rule,priority,k8s_ns_name) (count_over_time({source="syscall",priority=~"Emergency|Alert|Critical|Error"}' + pipe + ' | disposition="actionable" [30m])) > 0',
        'SecurityFalcoWarningBurst': 'sum by (hostname,rule,k8s_ns_name) (count_over_time({source="syscall",priority="Warning"}' + pipe + ' | disposition!="expected" [5m])) >= 20',
    }
    path = ROOT / '02a-config-loki-rules.yaml'
    content = path.read_text()
    for name, query in queries.items():
        content, n = re.subn(r'(- alert: '+name+r'\n)        expr:.*?(?=        for:)', lambda m: m[1]+'        expr: >-\n          '+query+'\n', content, flags=re.S)
        assert n == 1
    path.write_text(content)
    path = ROOT / 'grafana_dashboards/security-soc.json'
    dash = json.loads(path.read_text())
    if dash.get('version', 1) < 2:
        for panel in dash['panels']:
            if panel['id'] <= 16: panel['gridPos']['y'] += 5
        dash['version'] = 2
    for panel in dash['panels']:
        if panel['id'] == 10:
            panel['title'] = 'Runtime events requiring review'
            panel['description'] = 'Warning+ events excluding narrowly classified expected activity. Actionable means Error+ priority, not confirmed compromise. All source events remain in Loki.'
            panel['targets'][0]['expr'] = '{source="syscall",priority=~"Warning|Error|Critical|Alert|Emergency"}'+pipe+' | disposition!="expected"'
    dash['panels'] = [p for p in dash['panels'] if p['id'] not in [17,18,19,20]]
    ds = next(p['datasource'] for p in dash['panels'] if p['id']==10)
    for i,kind in enumerate(['actionable','expected','unreviewed']):
        dash['panels'].append(dict(id=17+i,title='Runtime '+kind+' (selected period)',type='stat',datasource=ds,gridPos=dict(x=i*8,y=0,w=8,h=5),description='Classification of Warning+ events. Expected matches a reviewed, expiring signature; actionable is unmatched Error+; unreviewed is unmatched Warning. Counts are events, not incidents or notifications.',targets=[dict(refId='A',expr='sum(count_over_time({source="syscall",priority=~"Warning|Error|Critical|Alert|Emergency"}'+pipe+' | disposition="'+kind+'" [$__range])) or vector(0)',queryType='instant')],options=dict(reduceOptions=dict(calcs=['lastNotNull'],fields='',values=False),textMode='value_and_name',colorMode='value'),fieldConfig=dict(defaults=dict(unit='short'),overrides=[])))
    dash['panels'].append(dict(id=20,title='Expected runtime evidence — retained, not paged',type='logs',datasource=ds,gridPos=dict(x=0,y=75,w=24,h=10),description='Original events retained for investigation. expected_reason identifies the reviewed policy entry. Expired entries no longer classify new events.',targets=[dict(refId='A',expr='{source="syscall"}'+pipe+' | disposition="expected"')],options=dict(showTime=True,showLabels=False,sortOrder='Descending',wrapLogMessage=True)))
    path.write_text(json.dumps(dash,indent=2)+'\n')

if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--pipeline',action='store_true');args=p.parse_args()
    if args.pipeline: print(pipeline())
    else: generate()
