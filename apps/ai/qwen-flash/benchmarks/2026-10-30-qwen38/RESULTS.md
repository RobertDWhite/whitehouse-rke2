# Qwen3.8 Flash Next on the Spark: benchmark results

Tested October 30, 2026 on rke2-node-10. Qwen3.6-35B-A3B remains the default. NVIDIA Qwen3.8-Flash-Next-FP4-Local completed the tests with speculative decoding disabled, but it did not improve throughput. It performed better on a small set of deterministic correctness checks; these results do not establish general coding superiority.

## Measured throughput

| Workload | Qwen3.6 MTP3, tok/s | Qwen3.8 without MTP, tok/s |
| --- | ---: | ---: |
| Coding decode | 121.6 | 25.6 |
| Four requests, aggregate | 219.3 | 42.7 |
| 20K-input coding decode | 122.2 | 25.5 |

Both models received the same prompts, temperature 0, seed 42, thinking disabled, and a 512-token output limit. Warmup was excluded. Decode throughput uses output tokens minus one divided by the interval between first and last streamed content. Aggregate throughput includes input processing and queueing. These are single runs, without confidence intervals; all speed responses reached the output limit and are not complete-task correctness evaluations. This compares the configurations that ran on this host, not identical inference algorithms.

Qwen3.8 short-prompt time to first token was 0.29–0.48 seconds. Of four simultaneously submitted requests, one waited 28.1 seconds for its first token. With approximately 19,850 input tokens, its first long prompt took 11.5 seconds to first token; subsequent requests reused the inventory prefix and took about 1.1 seconds. Prefix caching was enabled for both models. Consult the JSON records for each request and response.

## Correctness and compatibility

| Strict checks passed | Qwen3.6 | Qwen3.8 |
| --- | ---: | ---: |
| Thinking disabled | 3/8 | 6/8 |
| Thinking enabled, 2,048-token limit | 4/8 | 7/8 |
| Thinking enabled, 8,192-token limit | 6/8 | 7/8 |

The eight cases cover Python semantics, an LRU trace, a binary-search correction, SQL NULL behavior, Kubernetes scheduling/readiness, half-open intervals, and retry probability. Answers must match the expected JSON structure and values exactly. Generated code is never executed. These are smoke checks, not SWE-bench or a representative coding evaluation. Thinking was enabled through the model templates; their internal reasoning policies can differ, and Qwen3.8's template defaults to xhigh reasoning.

Qwen3.8's thinking-mode failure at both limits returned correct nested-list values inside a string instead of the required JSON array. Without thinking, it failed the LRU trace and merged intervals that only touch. All three API checks passed: JSON arithmetic, an automatic tool call with validated arguments, and retrieval from approximately 12K input tokens. Qwen3.6 passed those checks again after restoration through the normal LAN endpoint.

## Working configuration and limits

- Model: `nvidia/Qwen3.8-Flash-Next-FP4-Local`, revision `b9ce18a6887e18c089fdea8509a33e63429a3300`.
- Runtime: `eugr/spark-vllm-b12x@sha256:57c24fc397c66d4d28a7d0d5f1d792934fd01a70f4574cff6315fb7bc329d5cb`, ARM64, CUDA 13.0.2, vLLM `0.1.dev21830+g8244d1c44.d20261026`.
- Hardware: NVIDIA GB10; driver 580.159.03; 2,200 MHz clock cap; shared Kubernetes control-plane host.
- Text only, 32,768-token context, four scheduler slots, 1,024-token prefill batches, 2 GiB FP8 KV cache, PLE disk offload through io_uring, and no speculative draft model.
- B12X heuristic kernel selection, cold autotuning disabled, one compiler worker, CUDA allocator garbage collection threshold 0.6, unlimited memlock. Full arguments and environment are in `start_server.py` and `benchmark-pod.json`.

The successful load reported 66.8 GiB of model memory. The final resource sample showed about 13.0 GiB host MemAvailable, 68°C GPU temperature, 94% GPU utilization, and 2,184 MHz. This is a sample, not a peak or sustained-load characterization. A watchdog retained a 10 GiB host-memory reserve throughout. The staged checkpoint contains 21 shards totaling 102,192,011,716 bytes; all expected shard sizes were checked. Model and runtime caches remain available for subsequent work.

Ten startup configurations were tried. Initial failures exposed locked-memory requirements, startup memory peaks, cold autotuning overhead, one invalid CLI flag, a monitoring transport error, and an undersized KV cache. The corrected MTP3 configuration loaded its weights and kernels but crossed the reserve during JIT warmup. Removing the draft model allowed the final configuration to serve and complete every test. Attempt logs are retained. These results do not prove that MTP cannot run on a dedicated Spark with more available memory.

## Operational outcome

The temporary benchmark pod was removed, Qwen3.6 was restored, and Argo reconciliation resumed. The production pod is ready, the application is Synced/Healthy, and real production API checks passed. Other workloads and client defaults were preserved. No permanent deployment manifest was changed for this experiment.

Keep Qwen3.6 as the interactive default. Qwen3.8 can be considered for selected quality-sensitive work if its slower response rate and smaller tested context are acceptable. A general capability claim would require a larger task suite and repeated measurements.

Primary references: [NVIDIA model card](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-FP4-Local), [runtime project](https://github.com/eugr/spark-vllm-docker). The model card is archived locally as `model-card.md`.
