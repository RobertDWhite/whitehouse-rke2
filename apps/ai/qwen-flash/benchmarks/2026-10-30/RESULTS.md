# DGX Spark inference benchmark — 2026-10-30

## Result

NVIDIA Qwen3.6-35B-A3B-NVFP4 with three-token MTP speculative decoding reached 123.7 output tokens/second on the coding prompt. Four concurrent requests achieved 208.2 output tokens/second in aggregate. These are measurements on the existing GB10 Kubernetes node with its 2200 MHz clock cap retained.

The existing Qwen3.8-Flash-Next endpoint was unavailable before the experiment: its memory watchdog repeatedly killed vLLM at less than 4 GiB host MemAvailable. It could not provide a current throughput baseline. The comparisons below use the **same smaller model**, with MTP disabled or enabled. They do not establish a measured speedup relative to the user's prior 10s-of-tokens/second observation.

## Measurements

| Configuration | Per-request decode tokens/s | Aggregate end-to-end output tokens/s |
| --- | ---: | ---: |
| baseline-single | 73.5–74.6 | 73.1 |
| baseline-four | 45.2–45.2 | 177.3 |
| mtp-single | 93.2–123.7 | 92.7 |
| mtp-four | 55.2–72.5 | 208.2 |
| mtp-long-context | 88.1–116.4 | 76.2 |

Single-request rows contain three sequential prompts. Four-request rows contain four simultaneous requests. Per-request decode speed excludes initial prompt processing and is estimated as (completion_tokens - 1) / (last_content_time - first_content_time). Aggregate speed includes prompt processing and divides total generated output tokens by the measured wall time. Speculative streams may deliver multiple tokens per chunk; decode estimates are approximate.

The short prompts contain 55–66 input tokens and generate 512 output tokens each. Generation stops at that cap; these are speed samples. Complete task quality was not evaluated. Thinking is disabled, temperature is zero, and the seed is 42. A separate warmup request is excluded from each measurement. The four-request case includes two identical coding prompts. Prefix caching is enabled.

The long-context test supplies approximately 19,850 input tokens. Its first request had a 3.18-second time to first token; later requests reuse the inventory prefix and had approximately 0.6-second TTFT. Decode ranged from 88.1 to 116.4 tokens/second. The inventory is synthetic and does not reproduce an entire coding-agent conversation.

## Functional checks

- Correct JSON arithmetic output.
- Correct automatic selection of `get_node_status`, with valid JSON arguments.
- Correct extraction of a planted record from a 12,311-token inventory.

These smoke checks are not a model-quality comparison. The smaller model is not proven equivalent to Qwen3.8-Flash-Next on the user's tasks.

## Configuration

- Model: `nvidia/Qwen3.6-35B-A3B-NVFP4`
- Revision: `1355db6a052410cfd62085d94b58866fd0f2c3c5`
- Engine: existing GB10-compatible vLLM 0.31.0 image.
- Main MoE backend: Marlin; attention: FlashInfer; KV cache: FP8.
- MTP: 3 speculative tokens; draft MoE backend: Triton.
- Context limit: 131072; maximum sequences: 4; batched tokens: 8192.
- GPU memory utilization: 0.4; async scheduling and prefix caching enabled.
- Existing GPU clock cap retained; observed GPU temperatures 67–68 C after measurements.
- Approximately 36 GiB MemAvailable after measurements; CUDA allocations share host memory.

See `start_server.py` for the exact tested launch command and `bench.py` / `check_api.py` for reproducible requests. JSON files contain raw timing measurements and generated responses.

## Sources

- NVIDIA model card and Spark recipe: https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4
- NVIDIA performance article: https://developer.nvidia.com/blog/run-local-ai-agents-with-faster-models-and-multi-node-clustering-on-nvidia-dgx-spark/
- NVIDIA's Ling optimization article, including the synthetic/real-workload distinction: https://developer.nvidia.com/blog/3-ways-to-boost-moe-inference-on-nvidia-dgx-spark/

## Deployment decision

This is an isolated benchmark. No client model selection has been changed. The larger checkpoint is preserved. Promoting the smaller model requires an explicit model choice, a GitOps deployment, and updating clients to its honest model identifier (`qwen3.6-35b-a3b`).

## Cleanup

The temporary benchmark pod and loopback port-forward were removed. The original deployment was restored to one replica, and the Argo CD skip-reconcile annotation was removed. The original model's existing startup memory guard remains in place; restoring its configuration does not establish that it can serve successfully. The smaller checkpoint and compiled caches remain under `/hf/benchmark` on the existing local model volume for a future deployment. No client configuration was changed.
