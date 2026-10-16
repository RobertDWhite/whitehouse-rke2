# rke2-node-50 Join Runbook (RTX 5090, Ubuntu)

Run these steps on rke2-node-50 booted into Ubuntu.

## 1. Get cluster token (from any existing node or control plane)

```bash
# From your Mac via kubectl:
kubectl get secret -n kube-system rke2-join-token -o jsonpath='{.data.token}' 2>/dev/null | base64 -d \
  || ssh <control-plane-node> sudo cat /var/lib/rancher/rke2/server/token
```

## 2. Install NVIDIA Container Toolkit

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
```

## 3. Install and configure RKE2 agent

```bash
curl -sfL https://get.rke2.io | INSTALL_RKE2_TYPE=agent sudo sh -

sudo mkdir -p /etc/rancher/rke2
cat <<EOF | sudo tee /etc/rancher/rke2/config.yaml
server: https://<CONTROL_PLANE_IP>:9345
token: <TOKEN_FROM_STEP_1>
node-label:
  - "gpu=true"
EOF
```

## 4. Configure containerd to use NVIDIA runtime

```bash
sudo nvidia-ctk runtime configure --runtime=containerd \
  --config=/var/lib/rancher/rke2/agent/etc/containerd/config.toml.tmpl

# Verify the nvidia runtime was added:
grep -A3 nvidia /var/lib/rancher/rke2/agent/etc/containerd/config.toml.tmpl
```

## 5. Start the agent

```bash
sudo systemctl enable rke2-agent
sudo systemctl start rke2-agent

# Watch it join:
sudo journalctl -u rke2-agent -f
```

## 6. Verify from the cluster (back on your Mac)

```bash
kubectl get node rke2-node-50
kubectl describe node rke2-node-50 | grep -A5 "Capacity:"
# Should show: nvidia.com/gpu: 2  (2x time-sliced replicas)
```

## 7. Pull models on the new Ollama

Once the pod is running:
```bash
kubectl exec -n ai-stack deploy/ollama-5090 -- ollama pull llama3.1:8b
# Pull whatever models you want on the 5090 — they are stored separately from node-10's models
```

## Notes

- Node-10 Ollama (`http://ollama:11434`) remains the fallback — always available.
- When node-50 boots Windows, the `ollama-5090` pod is evicted after ~30s
  (tolerationSeconds set to 30 in deployment). Open WebUI falls back to node-10 automatically.
- `immich-ml-gpu` uses `nodeSelector: gpu: "true"` — it will prefer node-50 when available
  (scheduler will place it on the least-loaded GPU node).
- `localai` (SD) is currently replicas=0. To enable on the 5090, update the image from
  `l4t-arm64-cuda-13` to an amd64 CUDA image and set replicas=1.

## Local-path storage (NVMe)

`/opt/local-path-provisioner` is the Samsung 960 EVO 500 GB (`nvme1n1p1`, ext4, label
`k8s-local`), mounted from `/etc/fstab`:

```
UUID=bd2b5107-d5c6-443b-9a5a-d4715a8585e6 /opt/local-path-provisioner ext4 defaults,noatime,nofail 0 2
```

ComfyUI/InvokeAI models, listmonk and the Immich ML cache are `local-path` PVCs, and
local-path does not enforce PVC sizes. They used to sit on the 196 GB root disk, and a
model download filling it put the node into DiskPressure, which evicted every pod there
(Longhorn CSI included, so `ollama-5090` could not attach its volume). Keep them on the
NVMe. `nofail` lets the node boot if the drive is missing, but the pods would then write
to the root disk underneath the mountpoint, so check `findmnt /opt/local-path-provisioner`
after any hardware change.
