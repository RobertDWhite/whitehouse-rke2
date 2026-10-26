# Scanner worker log retention

`20-scan-log-retention.conf` is installed on **rke2-node-13 and rke2-node-50**, at
`/var/lib/rancher/rke2/agent/etc/kubelet.conf.d/20-scan-log-retention.conf`.
RKE2 loads this persistent drop-in on agent startup. It is tracked here but is
not distributed by an automatic host configuration controller.

The normal Kubernetes pod-log API exposes only the current container log file.
A verified SDR scan produced 13.9 MB of base64/bzip2 output and 107 MB of JSON;
10 MiB rotation discarded its beginning from that API. CRI timestamps and stream
framing add storage overhead. A 64 MiB file accommodates the archive's bounded
32 MiB encoded output, including that overhead. Two files bound nominal retained
logs to 128 MiB per container, excluding rotation overshoot. This setting applies
to all containers on the worker, not only scanner pods. Monitor node disk space.

The Trivy Application's `trivyOperator.scanJobNodeSelector` directs image scans
to nodes labeled `security.white.fm/scan-log-retention=64Mi`. The label must only be applied after effective kubelet config
has been verified; remove it before reverting that configuration. Adding another requires the same maintenance and schedulable CPU, memory and
disk checks. Node13 alone was CPU-reservation constrained; node50 supplies
additional capacity. This does not
claim every image architecture or node-collector workload is scanned there.

Before applying to another worker, inspect disk/memory headroom, existing
kubelet flags/drop-ins, workload/storage placement and recovery access. Preserve
existing settings. Do not restart multiple nodes together or ignore node12's
existing NotReady condition. Use a rollback timer when independently recoverable
access is uncertain. The first application used a five-minute host rollback timer,
then canceled it after service health, NodeReady and effective config checks.

After a controlled RKE2 agent restart, check the node's kubelet configz via the
Kubernetes API for `containerLogMaxSize: 64Mi` and `containerLogMaxFiles: 2`.
Compare workload UIDs/restarts, validate gateway/authentication and required
applications, and run the oversized exact-image scan. Confirm the automatic
archive retains the complete report after normal job expiry. Configuration and
pod readiness alone do not prove report retention.

Rollback: remove only this drop-in and restart the affected RKE2 agent during
controlled maintenance; verify effective previous settings and workload recovery.
Remove that node from scan placement if full reports no longer fit its log limit.
Keep the archive PVC and private evidence. Never disable TLS verification or grant
the archive service broad node-proxy access to work around rotated logs.

References: [RKE2 configuration](https://docs.rke2.io/install/configuration),
[Kubernetes kubelet configuration](https://kubernetes.io/docs/tasks/administer-cluster/kubelet-config-file/).
