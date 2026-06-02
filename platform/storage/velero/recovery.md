Disaster Recovery Runbook — Kubernetes + Velero

This document describes exact, step-by-step procedures to recover workloads using Velero + MinIO in this cluster.

It is written for high-stress situations. Follow steps in order. Do not skip ahead.

⸻

Assumptions
	•	Backups are stored in MinIO bucket: velero-backup
	•	Velero is installed via Argo CD / Helm
	•	Storage backend is Longhorn and/or Synology CSI
	•	Backups are created via Velero Schedules (GitOps)

⸻

What Velero DOES and DOES NOT Back Up

Velero DOES back up
	•	Namespaces
	•	Kubernetes objects (Deployments, Services, Secrets, etc.)
	•	PersistentVolumeClaims (PVCs)
	•	Volume snapshots (CSI-based)
	•	Application data

Velero DOES NOT back up
	•	Kubernetes cluster itself
	•	Node operating systems
	•	CNI, kubelet, control plane binaries
	•	Velero namespace (by design)
	•	Git repositories (Argo CD source of truth)

⸻

Scenario A — Cluster is Running, Applications Are Broken

Goal

Restore one or more namespaces without rebuilding the cluster.

Step 1 — List available backups

kubectl -n velero get backups

Choose the backup you want to restore (example: daily-critical-2026-01-19-030000).

⸻

Step 2 — Restore a single namespace (recommended)

apiVersion: velero.io/v1
kind: Restore
metadata:
  name: restore-immich
  namespace: velero
spec:
  backupName: daily-critical-2026-01-19-030000
  includedNamespaces:
    - immich

Apply it:

kubectl apply -f restore.yaml


⸻

Step 3 — Monitor restore progress

kubectl -n velero get restores
kubectl -n velero describe restore restore-immich

Success criteria:
	•	Phase: Completed
	•	No fatal errors
	•	Pods recreate
	•	PVCs bind

⸻

Scenario B — Cluster Is Completely Lost (Nodes Rebuilt)

This is the full disaster scenario.

High-level recovery order (DO NOT CHANGE ORDER)
	1.	Rebuild Kubernetes cluster
	2.	Reinstall storage layer
	3.	Restore MinIO
	4.	Reinstall Velero
	5.	Restore workloads

⸻

Step 1 — Rebuild Kubernetes Cluster
	•	Install Kubernetes (RKE2 / kubeadm / k3s)
	•	Ensure all nodes are Ready
	•	Confirm networking works

kubectl get nodes


⸻

Step 2 — Reinstall Storage Layer

You must reinstall storage before restoring data.

Options
	•	Longhorn
	•	Synology CSI

Verify storage is working:

kubectl get storageclass


⸻

Step 3 — Restore MinIO
	•	Deploy MinIO using the same data directory as before
	•	Ensure bucket exists: velero-backup
	•	Create a new MinIO service account

Old access keys are not required.

Verify bucket:

mc ls minio-direct/velero-backup


⸻

Step 4 — Reinstall Velero

Deploy Velero using GitOps (Argo CD).

Velero config must point to existing bucket:

bucket: velero-backup
s3Url: https://<MINIO_IP>:9000
s3ForcePathStyle: "true"
insecureSkipTLSVerify: "true"

Verify Velero is healthy:

kubectl -n velero get backupstoragelocations

Expected:

default   Available


⸻

Step 5 — Verify Historical Backups Are Visible

kubectl -n velero get backups

If backups appear → proceed.

If no backups appear → STOP. Check MinIO connectivity.

⸻

Step 6 — Restore Cluster Workloads

Recommended: Restore in stages
	1.	Databases
	2.	Core services
	3.	User-facing applications

⸻

Full cluster restore (excluding system namespaces)

apiVersion: velero.io/v1
kind: Restore
metadata:
  name: full-cluster-restore
  namespace: velero
spec:
  backupName: daily-critical-2026-01-19-030000
  excludedNamespaces:
    - velero
    - kube-system
    - kube-node-lease
    - kube-public

Apply:

kubectl apply -f restore.yaml


⸻

Restore Verification Checklist
	•	Namespaces recreated
	•	Pods start successfully
	•	PVCs bind
	•	Applications respond

Useful commands:

kubectl get pods -A
kubectl get pvc -A
kubectl describe restore <name>


⸻

Testing Recommendation (IMPORTANT)

At least once:
	1.	Delete a non-critical namespace
	2.	Restore it using Velero
	3.	Confirm application works

This validates the entire pipeline.

⸻

Common Failure Modes & Fixes

Symptom	Cause	Fix
SignatureDoesNotMatch	Reverse proxy / wrong endpoint	Use direct MinIO IP
NoSuchBucket	Bucket name mismatch	Fix bucket name
PVCs Pending	Storage not installed	Reinstall storage
Restore stuck	CRDs missing	Reinstall controllers


⸻

Key Takeaways
	•	Velero backups are off-cluster
	•	Storage must exist before restore
	•	GitOps + Velero = reproducible recovery
	•	Test restores prevent panic

⸻

Final Note

If this document is being used during an incident:
	•	Slow down
	•	Follow steps in order
	•	Verify each phase before proceeding

This runbook works. You tested it.