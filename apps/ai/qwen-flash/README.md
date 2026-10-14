# qwen-flash

Qwen3.8-Flash-Next (125B sparse MoE, 6B active) served by vLLM on node-10 (DGX Spark / GB10),
using the [blazux/qwen3.8-Flash-DGX](https://github.com/blazux/qwen3.8-Flash-DGX) recipe.
OpenAI-compatible API at `http://qwen-flash.qwen-flash.svc.cluster.local:8000/v1`, model
`qwen3.8-flash-next`, with tool calling and reasoning parsers enabled.

## How it fits on one GB10

The NVFP4 checkpoint is ~124 GiB, but 48 GiB of it is an n-gram embedding table that each
token reads only a few rows of. The recipe memory-maps that table from local NVMe, so
~75 GiB of weights live in the 128 GB unified pool. The rest of the pool goes to KV cache
(fp8), and page cache holds the hot rows of the table. That is why the model volume is
`local-path` on node-10 and not Longhorn.

node-10's Ollama was retired when this went in (Ollama traffic goes to the 5090 via
ollama-router). vLLM runs at the recipe's `--gpu-memory-utilization=0.80`, and it needs that
much memory free when it boots. Leave the remaining ~20% alone: it is the page cache that
holds the hot rows of the n-gram table, and prefill speed depends on it.

## Startup

1. `fetch-weights` downloads `nvidia/Qwen3.8-Flash-Next-NVFP4` at a pinned revision into the
   volume (resumable; a no-op once complete).
2. `prepare-hybrid` converts four shards' dense side layers to blockwise fp8, the recipe's
   recommended "hybrid" layout (+20% decode, same quality). It runs once (`.prepared` marker).
3. `vllm` loads ~75 GiB and compiles CUDA graphs. The first boot takes several minutes; compile
   caches persist on the volume.

## The image

The recipe patches the official `vllm/vllm-openai:v0.31.0` and compiles a GB10 (sm_121a)
top-k kernel, so it is built on node-10 itself and imported into node-10's containerd
(`imagePullPolicy: Never`). The in-cluster registry is too small for it (~20 GB).

To rebuild, for example after bumping the recipe commit, run a privileged BuildKit pod on
node-10:

```
buildctl-daemonless.sh build --frontend=dockerfile.v0 \
  --opt context=https://github.com/blazux/qwen3.8-Flash-DGX.git#<commit> \
  --opt filename=Dockerfile --opt platform=linux/arm64 \
  --output type=docker,name=registry.internal.white.fm/qwen38-flash-dgx:<tag>,dest=/out/qwen38-flash-dgx.tar
```

Use `moby/buildkit`, with `/out` as a hostPath under `/var/lib/qwen-flash-build`. Then import
the tarball on the node:

```
/var/lib/rancher/rke2/bin/ctr -a /run/k3s/containerd/containerd.sock -n k8s.io \
  images import /var/lib/qwen-flash-build/qwen38-flash-dgx.tar
```

Then update the image tag in `20-deployment.yaml`. If node-10 is ever rebuilt, the image has
to be rebuilt too.
