import datetime as dt
import pathlib
import sys
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from runtime_policy import classify
from maintenance import payload

NOW=dt.datetime(2026,11,11,tzinfo=dt.timezone.utc).timestamp()
RULE='Drop and execute new binary in container'
GOOD=dict(k8s_ns_name='immich',k8s_pod_name='immich-upload-to-synology-sync-29907097-q45hk',container='rsync',image='docker.io/library/alpine',process='rsync',exe='/usr/bin/rsync',parent='sh')
class Boundaries(unittest.TestCase):
    def test_expected(self):
        self.assertEqual(classify(GOOD,RULE,NOW),'immich-rsync')
    def test_unknown_workload_and_process_still_alert(self):
        for k,v in [('k8s_ns_name','other'),('k8s_pod_name','immich-server-123-abcd'),('exe','/tmp/rsync'),('parent','bash'),('image','dockerXio/library/alpine'),('container','server'),('process','curl')]:
            with self.subTest(k=k): self.assertEqual(classify({**GOOD,k:v},RULE,NOW),'')
    def test_expiry_is_exclusive(self):
        expiry=dt.datetime(2026,12,11,tzinfo=dt.timezone.utc).timestamp()
        self.assertEqual(classify(GOOD,RULE,expiry),'')
        self.assertEqual(classify(GOOD,RULE,expiry-1),'immich-rsync')
    def test_different_rule_never_suppressed(self):
        self.assertEqual(classify(GOOD,'Fileless execution via memfd_create',NOW),'')
    def test_missing_field_never_expected(self):
        for key in GOOD:
            fields=GOOD.copy();del fields[key]
            self.assertEqual(classify(fields,RULE,NOW),'')
    def test_host_logs_not_snapshot_cleanup(self):
        fields=dict(container='host',process='containerd',exe='/var/lib/rancher/rke2/data/v1.36.2-rke2r1-c4fd787d50a5/bin/containerd',parent='rke2',file='/var/lib/rancher/rke2/agent/containerd/io.containerd.snapshotter.v1.overlayfs/snapshots/123/fs/var/log/dpkg.log')
        self.assertEqual(classify(fields,'Clear Log Activities',NOW),'rke2-snapshot-log')
        for file in ['/var/log/dpkg.log','/var/log/auth.log','/var/lib/rancher/rke2/agent/containerd/io.containerd.snapshotter.v1.overlayfs/snapshots/123/fs/var/log/auth.log']:
            self.assertEqual(classify({**fields,'file':file},'Clear Log Activities',NOW),'')
    def test_nvidia_requires_hook_ancestry_and_workload(self):
        fields=dict(k8s_ns_name='immich',k8s_pod_name='immich-ml-gpu-abc-def',container='immich-ml',image='ghcr.io/immich-app/immich-machine-learning',process='memfd:runc_clon',exe='memfd:runc_cloned:ldconfig',parent='nvidia-ctk',runtime_hook='runc:[1:CHILD]')
        rule='Fileless execution via memfd_create'
        self.assertEqual(classify(fields,rule,NOW),'nvidia-immich-ml-gpu')
        for key,value in [('parent','bash'),('runtime_hook',''),('exe','memfd:payload'),('k8s_ns_name','other')]:
            self.assertEqual(classify({**fields,key:value},rule,NOW),'')
    def test_maintenance_limits(self):
        for alert,scope,minutes in [('SecurityKubeExec',['user=system:admin'],60),('SecurityFalcoHighPriority',['rule=x'],60),('SecurityKubeExec',['user=system:admin','namespace=falco'],241),('SecuritySourceMissing',['host=node-12'],60)]:
            with self.assertRaises(ValueError): payload(alert,scope,minutes,'Robert','test')
    def test_maintenance_exact_and_expiring(self):
        now=dt.datetime(2026,11,11,tzinfo=dt.timezone.utc)
        d=payload('SecurityKubeExec',['user=system:admin','namespace=falco'],30,'Robert','SOC validation',now)
        self.assertTrue(all(not m['isRegex'] for m in d['matchers']))
        self.assertEqual(dt.datetime.fromisoformat(d['endsAt'])-now,dt.timedelta(minutes=30))
if __name__=='__main__':unittest.main()
