---
pipeline_tag: image-text-to-text
base_model:
- Qwen/Qwen3.8-Flash-Next
license: other
license_name: nvidia-open-model-agreement
library_name: Model Optimizer
tags:
- nvidia
- ModelOpt
- Qwen3.8
- quantized
- FP4
- fp4
---


# Model Overview

**IMPORTANT**: the NVFP4 scale factors in this checkpoint are in a new lossless compression format, but upstream vLLM support for this format has not yet landed. **You must use one of the containers provided below.**

## Description:
The NVIDIA Qwen3.8-Flash-Next-FP4-Local model is the quantized version of Alibaba's Qwen3.8-Flash-Next model, which is an auto-regressive language model that uses an optimized transformer architecture. Qwen3.8-Flash-Next is a causal language model with a vision encoder, hybrid attention (Gated DeltaNet and Qwen Sparse Attention), Mixture-of-Experts, gated residual streams, and n-gram embeddings. For more information, please check [here](https://huggingface.co/Qwen/Qwen3.8-Flash-Next). The NVIDIA Qwen3.8-Flash-Next-FP4-Local model is quantized with [Model Optimizer](https://github.com/NVIDIA/Model-Optimizer) using [quantization-aware distillation (QAD)](https://developer.nvidia.com/blog/how-quantization-aware-training-enables-low-precision-accuracy-recovery/).

The checkpoint occupies approximately **96 GiB on disk** and is best suited for local AI hardware. It has been validated with vLLM on the following hardware: <br>
* **NVIDIA RTX PRO 6000 Blackwell** — single GPU, with PLE (n-gram embedding table) offload. 850K tokens of FP8 KV Cache <br>
* **NVIDIA DGX Spark** — Can run with or without PLE offload. 1M tokens of FP8 KV Cache with PLE offload <br>

This model is ready for commercial or non-commercial use.  <br>

## Third-Party Community Consideration
This model is not owned or developed by NVIDIA. This model has been developed and built to a third-party's requirements for this application and use case; see link to Non-NVIDIA [(Qwen3.8-Flash-Next) Model Card](https://huggingface.co/Qwen/Qwen3.8-Flash-Next) from Qwen.

### License/Terms of Use:
**GOVERNING DOWNLOAD TERMS:** Use of this model is governed by [NVIDIA Open Model Agreement](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-agreement/).

**ADDITIONAL INFORMATION** : The Qwen3.8-Flash-Next base model is governed by the [Qwen Community License 1.0](https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/main/LICENSE). **Qwen3.8-Flash-Next**.

### Deployment Geography:
Global <br>

### Use Case: <br>
Developers looking to take off-the-shelf, pre-quantized models for deployment in AI Agent systems, chatbots, RAG systems, and other AI-powered applications. <br>

### Release Date:  <br>
Hugging Face 10/05/2026 via https://huggingface.co/nvidia/Qwen3.8-Flash-Next-FP4-Local <br>

## References
NVIDIA Model Optimizer: https://github.com/NVIDIA/Model-Optimizer

## Model Architecture:
**Architecture Type:** Transformers  <br>
**Network Architecture:** Hybrid Attention (Gated DeltaNet and Qwen Sparse Attention) with Mixture-of-Experts (`Qwen4ExpForConditionalGeneration`) <br>
**Number of Model Parameters:** 125B in total and 6B activated, plus 51B n-gram embedding and 4B MTP <br>
**This model was developed based on [Qwen3.8-Flash-Next](https://huggingface.co/Qwen/Qwen3.8-Flash-Next)** <br>

## Input:
**Input Type(s):** Text, Image, Video <br>
**Input Format(s):** String, Red, Green, Blue (RGB), Video (MP4/WebM) <br>
**Input Parameters:** One-Dimensional (1D), Two-Dimensional (2D), Three-Dimensional (3D) <br>
**Other Properties Related to Input:** Context length up to 262K natively and extensible up to 1,000,000 tokens. <br>

## Output:
**Output Type(s):** Text <br>
**Output Format:** String <br>
**Output Parameters:** One-Dimensional (1D): Sequences <br>
**Other Properties Related to Output:** None <br>

Our AI models are designed and/or optimized to run on NVIDIA GPU-accelerated systems. By leveraging NVIDIA's hardware (e.g. GPU cores) and software frameworks (e.g., CUDA libraries), the model achieves faster training and inference times compared to CPU-only solutions. <br>

## Software Integration:
**Supported Runtime Engine(s):** <br>
* **vLLM** <br>

**Supported Hardware Microarchitecture Compatibility:** <br>
* NVIDIA Blackwell <br>

**Preferred Operating System(s):** <br>
* Linux <br>

The integration of foundation and fine-tuned models into AI systems requires additional testing using use-case-specific data to ensure safe and effective deployment. Following the V-model methodology, iterative testing and validation at both unit and system levels are essential to mitigate risks, meet technical and functional requirements, and ensure compliance with safety and ethical standards before deployment.

## Model Version(s):
The model version is FP4 QAD 1.0 and is quantized with nvidia-modelopt **v0.47.0** <br>

## Training and Evaluation Datasets:

## Calibration Dataset:
**Link:** Subset of [Nemotron-Post-Training-Dataset-v3](https://huggingface.co/collections/nvidia/nemotron-post-training-v3), [Nemotron-Post-Training-Dataset-v2](https://huggingface.co/datasets/nvidia/Nemotron-Post-Training-Dataset-v2) and subset of [Nemotron-Pretraining-Dataset](https://huggingface.co/collections/nvidia/nemotron-pre-training-datasets) <br>
**Data Collection Method by dataset:** Automated. <br>
**Labeling Method by dataset:** Automated. <br>
**Properties:**  NVIDIA's Nemotron-Post-Training-v3 is an open collection of approximately 50 curated Supervised Fine-Tuning (SFT) and Reinforcement Learning (RL) datasets designed for the Nemotron 3 model family (Nano, Super, and Ultra). NVIDIA's  Nemotron-Post-Training-Dataset-v2 is a post-training dataset curated by NVIDIA containing multi-turn conversations across diverse topics. The Nemotron-Pre-Training-Datasets collection on Hugging Face provides large-scale data corpora used to train the NVIDIA Nemotron model family <br>

## Training Dataset:
**Link**: Subset of [Nemotron-Post-Training-Dataset-v3](https://huggingface.co/collections/nvidia/nemotron-post-training-v3), [Nemotron-Post-Training-Dataset-v2](https://huggingface.co/datasets/nvidia/Nemotron-Post-Training-Dataset-v2) and subset of [Nemotron-Pretraining-Dataset](https://huggingface.co/collections/nvidia/nemotron-pre-training-datasets); only prompts from the datasets were used for data synthesis. <br>
**Data Modality:** Text <br>
**Text Training Data Size:** 500 Million Tokens, repeated for 3 epochs <br>
**Data Collection Method by dataset:** Hybrid: Automated, manually-collected, Synthetic <br>
**Labeling Method by dataset:** Hybrid: Automated, manually-labelled, Synthetic <br>
**Properties:** The Qwen3.8-Flash-Next-FP4-Local model was trained exclusively on a post-training corpus includes synthetic data for reasoning, code, science, tool use, instruction following, structured outputs, and multilingual tasks. Dataset disclosures are published in the `nvidia/nemotron-post-training-v2` and subset of `nvidia/nemotron-post-training-v3` and subset of `nvidia/nemotron-pre-training-datasets` Hugging Face collections. <br>

## Inference:
**Acceleration Engine:** vLLM
**Test Hardware:** NVIDIA DGX Spark and NVIDIA RTX PRO 6000 Blackwell

## Evaluation

**Datasets:** GPQA Diamond, ToolEvalBench <br>
**Data Collection Method by dataset:** Hybrid: Automated, Manually-Collected <br>
**Labeling Method by dataset:** Hybrid: Manually-Labeled, Automated <br>
**Properties:** GPQA Diamond contains 198 graduate-level multiple-choice questions written by domain experts in biology, physics, and chemistry. ToolEvalBench is a tool-calling quality benchmark that runs deterministic multi-turn agent scenarios against mock tools, scoring tool selection, parameter precision, multi-step chains, error recovery, and structured output. <br>

The accuracy benchmark results are presented in the table below:

| Precision | GPQA Diamond | ToolEvalBench |
|:---|:---:|:---:|
| BF16 | 92.2% | 89.4% |
| **FP4-Local** | **91.5%** | **88.7%** |

> Baseline: [Qwen3.8-Flash-Next](https://huggingface.co/Qwen/Qwen3.8-Flash-Next).

## Quantization-Aware Distillation
The BF16 teacher generates responses and supplies token-level probability and hidden-state targets. The trainable NVFP4 and MXFP8 weights are quantized and reconstructed for each forward pass. Gradients update the underlying weights so their low-precision representations better match the teacher.

The objective combines next-token probability matching using total variation, hidden-state matching, and separate losses for end-of-response and end-of-thinking behavior. These boundary losses target the teacher's decisions to continue, finish reasoning, and stop responding.

Training uses the Quatrain distillation trainer: 2,500 trunk updates followed by 1,500 joint-refinement updates, which also train the n-gram embedding tables and simulate MXFP8 shared-expert weights. Attention remains frozen, using its MXFP8 weights during joint refinement.

### What's quantized

| Component | Stored Precision |
|:---|:---|
| Decoder routed experts | QAD NVFP4; E2M1 values, E4M3 block scales, FP32 global/input scales |
| Decoder attention | MXFP8 projection selection from the pinned source |
| MTP routed experts | NVFP4, BF16 activations; block scales stored directly |
| Vision attention linears | MXFP8 E4M3 values; UE8M0 scales per 32 weights; 54 matrices |
| Vision MLP and merger linears | NVFP4 E2M1 values; E4M3 scales per 16 weights and FP32 global scale; 56 matrices; BF16 activations |
| Other vision tensors | BF16; 223 tensors, including normalization, biases, position embeddings, and convolutions |
| Normalization parameters | 100 FP32 tensors (97 hyperconnection and 3 PLE norms); 203 BF16 tensors; original bytes preserved |

## Inference:
**Acceleration Engine:** **vLLM** <br>
**Test Hardware:** **NVIDIA DGX Spark and NVIDIA RTX PRO 6000 Blackwell** <br>

## Usage

This checkpoint requires community vLLM containers (provided in cooperation with the "Local Inference Lab" community organization) until all features are available in upstream vLLM.

### NVIDIA DGX Spark

vLLM container: [`eugr/spark-vllm-b12x:latest`](https://hub.docker.com/r/eugr/spark-vllm-b12x/tags?name=latest)

Start the container. Set `HF_TOKEN` in the host environment first.

```sh
mkdir -p $HOME/.cache/huggingface $HOME/.cache/vllm $HOME/.cache/flashinfer $HOME/.triton $HOME/.cache/b12x

docker run --rm -it \
  --gpus all \
  --ipc host \
  --network host \
  --shm-size=16g \
  --ulimit memlock=-1 \
  --ulimit stack=67108864 \
  --security-opt seccomp=unconfined \
  --security-opt apparmor=unconfined \
  -e HF_TOKEN=$HF_TOKEN \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -v $HOME/.cache/vllm:/root/.cache/vllm \
  -v $HOME/.cache/flashinfer:/root/.cache/flashinfer \
  -v $HOME/.triton:/root/.triton \
  -v $HOME/.cache/b12x:/root/.cache/b12x \
  --entrypoint /bin/bash \
  eugr/spark-vllm-b12x:latest
```

Then launch vLLM inside the container:

```sh
export VLLM_PLE_TABLE_MEMORY="disk"
export B12X_DISK_BACKEND="io_uring"
export CUTE_DSL_ARCH="sm_121a"
export SAFETENSORS_FAST_GPU="1"
export VLLM_WORKER_MULTIPROC_METHOD="spawn"
export VLLM_SSM_CONV_STATE_LAYOUT="DS"
export VLLM_USE_AOT_COMPILE="1"
export VLLM_USE_MEGA_AOT_ARTIFACT="1"
export VLLM_USE_V2_MODEL_RUNNER="1"
export B12X_POLICY_MODE="auto"
export VLLM_MXFP8_LM_HEAD="1"
export VLLM_B12X_MOE_FP4_LAYER_MAX_INPUT_SCALE="w13"
export VLLM_B12X_MOE_FP4_FORCE_A16=1
export B12X_W4A16_A4_PREFILL=1

vllm serve nvidia/Qwen3.8-Flash-Next-FP4-Local \
    --host 0.0.0.0 \
    --port 8000 \
    --tensor-parallel-size 1 \
    --pipeline-parallel-size 1 \
    --mamba-cache-mode align \
    --enable-prefix-caching \
    --enable-chunked-prefill \
    --dtype bfloat16 \
    --kv-cache-dtype fp8 \
    --quantization modelopt_mixed \
    --block-size 16 \
    --load-format b12x \
    --max-model-len 262144 \
    --max-num-seqs 8 \
    --max-num-batched-tokens 4096 \
    --speculative-config '{"method":"mtp","num_speculative_tokens":4}' \
    --gdn-decode-kernel b12x \
    --linear-backend b12x \
    --moe-backend b12x \
    --no-enable-flashinfer-autotune \
    --mm-encoder-tp-mode data \
    --reasoning-parser qwen3 \
    --tool-call-parser qwen3_xml \
    --enable-auto-tool-choice \
    --compilation-config '{"pass_config":{"fuse_act_quant":true}}' \
    --gpu-memory-utilization 0.8
```

### NVIDIA RTX PRO 6000 Blackwell

vLLM container: [`ghcr.io/local-inference-lab/vllm:release-10152026`](https://github.com/local-inference-lab/blackwell-llm-docker/pkgs/container/vllm)

Simplified run command:

```sh
  docker run -d --name qwen38-tp1 --init --gpus '"device=0"' \
    --restart on-failure --network host --ipc host --shm-size 32g --stop-timeout 30 \
    --ulimit memlock=-1 --ulimit stack=67108864:67108864 \
    -v model-cache:/root/.cache/huggingface -v qwen38-runtime:/cache \
    -e PROFILE=qwen38-flash-next -e PORT=8000 \
    ghcr.io/local-inference-lab/vllm:release-10152026
```

Two step verbose commands:

Start the container. Set `HF_TOKEN` in the host environment first.

```sh
docker run --rm -it \
  --gpus all \
  --ipc host \
  --network host \
  --shm-size=16g \
  --ulimit memlock=-1 \
  --ulimit stack=67108864 \
  -e HF_TOKEN=$HF_TOKEN \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  --entrypoint /bin/bash \
  ghcr.io/local-inference-lab/vllm:release-10152026
```

Then launch vLLM inside the container:

```sh
export B12X_DENSE_SPLITK_TURBO=1
export B12X_DYNAMIC_DETERMINISTIC_OUTPUT=0
export B12X_DYNAMIC_DIRECT_EXPERT_SCALES=1
export B12X_DYNAMIC_SKIP_SPLIT_BARRIER_RESET=1
export B12X_DYNAMIC_SPLIT_COMPUTE_MAC=224
export B12X_DYNAMIC_SPLIT_FAST_PREPARE=1
export B12X_DYNAMIC_SPLIT_LOW_SMEM=1
export B12X_DYNAMIC_SPLIT_ROUTE_COMPUTE=1
export B12X_DYNAMIC_WORK_SOURCE=persistent_grid
export B12X_W4A16_A4_PREFILL=1
export B12X_DISK_BACKEND=io_uring
export B12X_MHC_PDL=1
export B12X_PCIE_ONESHOT_BLOCK_LIMIT=4
export B12X_PCIE_ONESHOT_PDL=1
export B12X_PCIE_ONESHOT_THREADS=512
export CUDA_BINARY_LOADER_THREAD_COUNT=8
export CUDA_DEVICE_ORDER=PCI_BUS_ID
export CUDA_MODULE_LOADING=LAZY
export CUTE_DSL_ARCH=sm_120a
export INSTANTTENSOR_BACKEND=BUFFERED
export NCCL_BUFFSIZE=2097152
export NCCL_CUMEM_ENABLE=0
export NCCL_IB_DISABLE=1
export NCCL_MAX_NCHANNELS=16
export NCCL_MIN_NCHANNELS=16
export NCCL_NET_PLUGIN=spcx
export NCCL_P2P_LEVEL=SYS
export NCCL_PROTO=LL,LL128,Simple
export OMP_NUM_THREADS=2
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export SAFETENSORS_FAST_GPU=1
export TORCHINDUCTOR_LOOP_ORDERING_AFTER_FUSION=0
export TORCH_CUDA_ARCH_LIST=12.0a
export VLLM_B12X_DENSE_ACTIVATION_MODE=auto
export VLLM_B12X_MOE_FP4_FORCE_A16=1
export VLLM_CAUSAL_CONV1D_UPDATE_HOIST=1
export VLLM_COMPUTE_NANS_IN_LOGITS=0
export VLLM_DISABLED_KERNELS=MarlinFP8ScaledMMLinearKernel
export VLLM_ENABLE_PCIE_ALLREDUCE=1
export VLLM_GDN_SPEC_DECODE_METADATA_FASTPATH=1
export VLLM_LM_HEAD_A16=1
export VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS=0
export VLLM_MTP_NVFP4_LM_HEAD=1
export VLLM_MXFP8_LM_HEAD=0
export VLLM_PCIE_ALLREDUCE_BACKEND=b12x
export VLLM_PLE_CPU_OFFLOAD=1
export VLLM_QWEN3_8_FLASH_NEXT_HC_TP=0
export VLLM_QWEN3_8_FLASH_NEXT_MTP_COMPACT=1
export VLLM_QWEN3_8_FLASH_NEXT_OVERLAP=1
export VLLM_SSM_CONV_STATE_LAYOUT=DS
export VLLM_USE_FASTOKENS=1
export VLLM_USE_V2_MODEL_RUNNER=1
export VLLM_WORKER_MULTIPROC_METHOD=spawn
export TORCH_ALLOW_TF32_CUBLAS_OVERRIDE=1
export TORCH_NCCL_USE_COMM_NONBLOCKING=0

vllm serve nvidia/Qwen3.8-Flash-Next-FP4-Local \
    --host 0.0.0.0 \
    --port 8000 \
    --async-scheduling \
    --block-size 64 \
    --compilation-config '{"cudagraph_mode":"FULL_AND_PIECEWISE","custom_ops":["all"]}' \
    --decode-context-parallel-size 1 \
    --dtype bfloat16 \
    --enable-auto-tool-choice \
    --enable-chunked-prefill \
    --no-enable-flashinfer-autotune \
    --enable-prefix-caching \
    --enable-prompt-tokens-details \
    --gdn-decode-kernel b12x \
    --gpu-memory-utilization 0.975 \
    --kv-cache-dtype fp8 \
    --no-language-model-only \
    --linear-backend b12x \
    --load-format instanttensor \
    --mamba-cache-mode align \
    --mamba-ssm-cache-dtype auto \
    --max-cudagraph-capture-size 64 \
    --max-model-len 262144 \
    --max-num-batched-tokens 6019 \
    --max-num-seqs 16 \
    --mm-encoder-tp-mode data \
    --mm-processor-cache-gb 0.0 \
    --moe-backend b12x \
    --pipeline-parallel-size 1 \
    --quantization modelopt_mixed \
    --reasoning-parser qwen3 \
    --speculative-config '{"method":"mtp","draft_sample_method":"probabilistic","rejection_sample_method":"standard","moe_backend":"b12x","num_speculative_tokens":3}' \
    --tensor-parallel-size 1 \
    --tool-call-parser qwen3_xml
```

## Ethical Considerations

NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications.  When downloaded or used in accordance with our terms of service, developers should work with their internal model team to ensure this model meets requirements for the relevant industry and use case and addresses unforeseen product misuse.

For more detailed information on ethical considerations for this model, please see the Model Card++ Explainability, Bias, Safety & Security, and Privacy Subcards below.

Please report model quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://www.nvidia.com/en-us/support/submit-security-vulnerability/).

SUBCARDS:
## Explainability


| Field                                    | Response                                                                                                                       |
|:----------------------------------------:|:-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------:|
| **Intended Task/Domain:**                | Text generation, reasoning, summarization, and question answering.                                                                                                                                                                                                                                                                                                                                                                                       |
| **Model Type:**                          | Text and Image-to-text transformer                                                                                                                                                                                                                                                                                                                                                                                                                       |
| **Intended Users:**                      | This model is intended for developers, researchers, and customers building/utilizing LLMs, while balancing accuracy and efficiency.                                                                                                                                                                                                                                                                                                                     |
| **Output:**                              | Text String(s)                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| **Describe how the model works:**        | Generates text by predicting the next word or token based on the context provided in the input sequence using multiple self-attention layers.                                                                                                                                                                                                                                                                                                            |
| **Name the adversely impacted groups this has been tested to deliver comparable outcomes regardless of:**        | Not Applicable    |
| **Technical Limitations & Mitigation:**  | The model was trained on data that contains toxic language and societal biases originally crawled from the internet. Therefore, the model may amplify those biases and return toxic responses especially when prompted with toxic prompts. Therefore, before deploying any applications of this model, developers should perform safety testing and tuning tailored to their specific applications of the model.                                       |
| **Verified to have met prescribed NVIDIA quality standards:** | Yes                                                                                                                                                                                                                                                                                                                                                                                                                                |
| **Performance Metrics:**                 | Accuracy, Throughput, and user-side throughput                                                                                                                                                                                                                                                                                                                                                                                                           |
| **Potential Known Risk**                 | The model may generate answers that may be inaccurate, omit key information, or include irrelevant or redundant text producing socially unacceptable or undesirable text, even if the prompt itself does not include anything explicitly offensive.                                                                                                                                                                                                       |
| **Licensing:**                           | **GOVERNING DOWNLOAD TERMS:** Use of this model is governed by [NVIDIA Open Model Agreement](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-agreement/).

**ADDITIONAL INFORMATION** : The Qwen3.8-Flash-Next base model is governed by the [Qwen Community License 1.0](https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/main/LICENSE).                                                                                                                            |


## Bias

| Field                                                                                                           | Response |
|:---------------------------------------------------------------------------------------------------------------:|:--------:|
| Participation considerations from adversely impacted groups [protected classes](https://www.senate.ca.gov/protected-classes) in model design and testing:       | None     |
| Measures taken to mitigate against unwanted bias                                                                | None     |


## Safety & Security

| Field                              | Response                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
|:-----------------------------------:|:--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------:|
| Model Application Field(s):         | Chat, Instruction Following, Chatbot Development, Code Generation, Reasoning                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Describe the life critical impact (if present) | Not Applicable                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Use Case Restrictions:              | Abide by the **GOVERNING DOWNLOAD TERMS:** Use of this model is governed by [NVIDIA Open Model Agreement](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-agreement/).

**ADDITIONAL INFORMATION** : The Qwen3.8-Flash-Next base model is governed by the [Qwen Community License 1.0](https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/main/LICENSE).                                                                                                                                                                                                                   |
| Model and Dataset Restrictions:     | The Principle of least privilege (PoLP) is applied limiting access for dataset generation. Restrictions enforce dataset access during training, and dataset license constraints adhered to. Model checkpoints are made available on Hugging Face, and may become available on cloud providers' model catalog.                                                                                                                                                                                                                                   |


## Privacy


| Field                                                                          | Response                                                                           |
| ------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------- |
| **Generatable or reverse-engineerable personal data**                          | No 
| **Personal data used to create this model**                                    | None Known                                                                         |      |
| **Was consent obtained for any personal data used**                            | Not Applicable                                                                     |
| **How often is the dataset reviewed**                                          | Before Release                                                                     |
| **Was data from user interactions with the AI model used to train the model**  | No                                                                                 |
| **Is there provenance for all datasets used in training**                      | Yes                                                                                |
| **Does data labeling comply with privacy laws**                                | Yes                                                                                |
| **Is the data compliant with data-subject requests for correction or removal** | No, not possible with externally-sourced data.                                     |
| **Applicable NVIDIA Privacy Policy**                                           | [NVIDIA Privacy Policy](https://www.nvidia.com/en-us/about-nvidia/privacy-policy/) |