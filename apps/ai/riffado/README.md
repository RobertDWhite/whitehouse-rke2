# Riffado (Plaud transcription companion)

Self-hosted companion for the Plaud Note recorder: syncs recordings from
Plaud's cloud, transcribes them through an OpenAI-compatible API and stores
audio + transcripts on infrastructure we control. Upstream:
https://github.com/riffado/riffado (AGPL-3.0).

## Layout

| Piece | Where | Why |
| --- | --- | --- |
| `riffado` (Next.js app) | Deployment, `riffado.internal.white.fm` | The app itself. |
| `riffado-postgres` | StatefulSet on Longhorn | Postgres 16. Longhorn, not NFS: Postgres on NFS is a corruption risk. |
| `riffado-postgres-logical-backup` | CronJob 05:40 daily | `pg_dump` to the Synology so the DB is recoverable even if Longhorn is not. 14-day retention. |
| `speaches` | Deployment, GPU on rke2-node-50 | faster-whisper behind an OpenAI-compatible `/v1/audio/transcriptions`. Loads `large-v3-turbo` in fp16 and keeps it resident. |
| `nemotron-transcribe` | Deployment, GPU on rke2-node-50 | NVIDIA Nemotron 3.5 Transcribe (NVFP4) on an on-demand vLLM, with speaker labels. See below. |
| Audio + backups | NFS `10.100.1.20:/volume1/Personal`, subPaths `Riffado/audio`, `Riffado/db-backups` | Permanent storage on the NAS, same share Gramps uses. |

Summaries use the GB10 Ollama (`http://ollama.ai-stack.svc.cluster.local:11434/v1`).

## Why speaches is on node-50 and not the GB10

The arm64 speaches image ships a CPU-only CTranslate2 ("This CTranslate2
package was not compiled with CUDA support", verified 2026-09-19 on
rke2-node-10). whisper.cpp's CUDA image is amd64-only as well. The 5090 on
rke2-node-50 runs the standard amd64 CUDA image, so that is where Whisper
lives. node-50's time-slicing was raised 4 -> 6 to fit it next to
comfyui/invokeai/ollama-5090/immich-ml.

## First run

1. Open https://riffado.internal.white.fm/register and create the one account,
   then set `DISABLE_REGISTRATION: "true"` in `20-deployment.yaml`.
2. Settings -> AI providers -> Add provider (Custom):
   - Transcription: base URL `http://speaches.riffado.svc.cluster.local:8000/v1`,
     model `deepdml/faster-whisper-large-v3-turbo-ct2`, any non-empty API key.
   - Summaries: base URL `http://ollama.ai-stack.svc.cluster.local:11434/v1`,
     model `qwen3.8:27b`, any non-empty API key.
3. Connect Plaud with the email OTP flow (Google/Apple sign-in accounts need the
   Connector extension instead, see upstream docs).

Secrets (`11-secret.sops.yaml`): `POSTGRES_PASSWORD`, `DATABASE_URL`,
`BETTER_AUTH_SECRET`, `ENCRYPTION_KEY`. Losing `ENCRYPTION_KEY` loses every
stored token/API key/transcript, so it is in the SOPS file and nowhere else.

## Speaker-labelled transcripts (Nemotron 3.5 Transcribe)

speaches returns plain text, so speaker-labelled transcripts come from
`nemotron-transcribe`: NVIDIA's
[Nemotron 3.5 Transcribe](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-NVFP4)
(30B-A4B, NVFP4: the only precision NVIDIA supports on the RTX 5090) on vLLM
0.32 with NeMo Speech at the commit in `54-nemotron-deployment.yaml`. It
replaced VibeVoice-ASR on 2026-10-29.

[`nemotron/adapter.py`](nemotron/adapter.py) puts an OpenAI-compatible
`/v1/audio/transcriptions` in front of vLLM's chat endpoint and returns the same
shape VibeVoice did: `[hh:mm:ss] Speaker N` blocks in `text`, plus `segments`,
`language` and `duration`. Speaker turns come from the model's `<spk:N>` tags
and the word timings from its CTC timestamp head. It decodes any audio with
PyAV, sends up to ~59 min per request (the model's limit) and splits longer
recordings near a pause; speaker numbers restart in each piece. The model tags
at most four speakers.

**On demand.** vLLM only runs while there is work. A request unloads
ollama-5090's models, starts vLLM (several minutes from cold), transcribes, and vLLM stops after
`IDLE_SECONDS` (10 min), so idle VRAM is ~0 and the 5090 stays usable for
ollama/comfyui. vLLM takes 80% of the 5090 (~26 GiB) while it runs, leaving room
for speaches. Requests are processed one at a time; others wait their turn.

**Storage.** The 20 GiB checkpoint is on node-50's local disk
(`nemotron-models`, local-path): from Longhorn it loaded at 8-12 MB/s, 15 min
per cold start. The venv (~15 GiB) and compile caches are on Longhorn
(`nemotron-transcribe`), because node-50's root disk (~86% full, kubelet image
GC at 85%) has no room for both. The init container builds the venv and
downloads the checkpoint on first start (or after bumping `NEMO_COMMIT` /
`MODEL_REVISION`); otherwise it is a no-op. No image to build: the pod runs the
stock `ghcr.io/astral-sh/uv` image and the scripts come from a ConfigMap.

Riffado provider: Custom, base URL
`http://nemotron-transcribe.riffado.svc.cluster.local/v1`, model
**`nemotron-transcribe`** (a name containing `diarize` takes Riffado's OpenAI
diarization path). The API key is `NEMOTRON_API_KEY` in `11-secret.sops.yaml`.
`WHISPER_REQUEST_TIMEOUT_MS` is 6 h. An interrupted run (pod restart, node-50
rebooting to the desktop) starts over.

To backfill older recordings, run
[`scripts/transcribe-batch.py`](scripts/transcribe-batch.py) from a machine with
cluster access:

```sh
kubectl -n ai-stack port-forward svc/ollama-5090 11434:11434 &
python3 apps/ai/riffado/scripts/transcribe-batch.py              # all not yet on Nemotron
python3 apps/ai/riffado/scripts/transcribe-batch.py <id> [<id>]  # these, even if done
```

It transcribes each recording and re-runs every summary. Riffado lists these
transcripts with provider `browser` and model `nemotron-transcribe`; the
Re-transcribe button works too.

**Bun idle timeout:** v0.6.4 runs on Bun, whose fetch drops a response that
sends nothing for 300 s, whatever `WHISPER_REQUEST_TIMEOUT_MS` says. The
transcriber sends nothing until the transcript is done, so every request over
~5 min was lost. Summaries hit the same cutoff.
`BUN_CONFIG_HTTP_IDLE_TIMEOUT=21600` (seconds) in `20-deployment.yaml` lifts it;
verified on Bun 1.3.14 in the Riffado image (2026-10-27).

Manual uploads in v0.6.4 are cut off at 10 MiB by the Next.js middleware despite
the 500 MiB the UI advertises. Plaud sync downloads server-side and is
unaffected.
