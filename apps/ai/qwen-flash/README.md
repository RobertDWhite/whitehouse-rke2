# Spark inference

The `qwen-flash` service runs NVIDIA Qwen3.6-35B-A3B-NVFP4 on the DGX Spark (GB10, node-10). The OpenAI-compatible API remains at `http://qwen-flash.qwen-flash.svc.cluster.local:8000/v1`, with LAN access through `http://10.99.5.128:8000/v1`. The served model identifier is **`qwen3.6-35b-a3b`**.

The deployment follows [NVIDIA's Spark recipe](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4): Marlin NVFP4 weights, FlashInfer attention, FP8 KV cache, and three-token MTP speculative decoding with a Triton draft backend. Async scheduling and prefix caching are enabled. The context limit is 131072 tokens, with up to four sequences and an 8192-token prefill batch.

## Memory and startup

The model checkpoint occupies approximately 22 GiB. vLLM uses a 0.4 GPU memory budget. Because CUDA and Linux share the same physical memory, the startup guard requires 64 GiB of available host memory, and a watchdog stops vLLM below 8 GiB. The existing host GPU clock cap remains at 2200 MHz. The pod requests 48 GiB and has a 64 GiB memory limit; the host watchdog also covers CUDA allocations outside cgroup accounting.

The init container downloads the pinned checkpoint revision `1355db6a052410cfd62085d94b58866fd0f2c3c5` into `/hf/benchmark`, reusing the benchmark cache. Compilation caches persist on the existing local model PVC. The older Qwen3.8-Flash-Next weights remain on that PVC for rollback.

The deployment reuses the tested local ARM64 vLLM 0.31.0 image, `registry.internal.white.fm/qwen38-flash-dgx:v0.31-1139abf`. Despite the image name, it supports the smaller model. It uses `imagePullPolicy: Never`: rebuilding the Spark requires rebuilding/importing this image from the [original recipe](https://github.com/blazux/qwen3.8-Flash-DGX/tree/1139abf38990583d3f5503ea3a7525e791468567) before starting the deployment.

## Validation

[The benchmark report](benchmarks/2026-10-30/RESULTS.md) includes raw timings and reproducible requests. The smaller model achieved 124 output tokens/sec on a short coding prompt and 208 tokens/sec across four requests. Other single-request prompts achieved 93–96 tokens/sec. A roughly 20K-token coding input achieved 116 output tokens/sec.

These tests disabled thinking through `chat_template_kwargs.enable_thinking=false`; clients can choose their reasoning behavior. The deployment does not force that request setting. Short throughput tests capped generation at 512 output tokens. Tool-call, JSON-output, and context-retrieval smoke checks passed; model-quality equivalence to the larger checkpoint has not been established.

## Clients and rollback

OpenCode, App Store Reviews, and Congress Trades use the new served identifier. The service addresses remain unchanged. Clients managed outside Git must also select `qwen3.6-35b-a3b`. Hermes and the FreshRSS BI pipeline currently select separate Ollama models and retain those selections.

Rollback requires reverting the deployment and client-model changes together. The older model previously failed its startup memory budget on this shared node; reverting its configuration alone does not establish that it can serve successfully.
