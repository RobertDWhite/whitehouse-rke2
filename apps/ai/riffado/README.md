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
| `vibevoice` | Deployment, GPU on rke2-node-50 | VibeVoice-ASR with speaker labels; Riffado's default transcription provider. See below. |
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

## Speaker-labelled transcripts (VibeVoice)

speaches returns plain text, so Plaud sync transcribes with `vibevoice`
instead. It runs Microsoft's
[VibeVoice-ASR](https://huggingface.co/microsoft/VibeVoice-ASR) through
[swallace17/vibevoice-k8s](https://github.com/swallace17/vibevoice-k8s) at
`f5fb110`. It returns `[hh:mm:ss] Speaker N` blocks in the transcript text. It
splits recordings over 12 minutes into ~10-minute chunks near pauses and matches
voices across the chunks. Speaker numbers are anonymous and apply to one
recording only.

Its model peaks at ~21.5 GiB of VRAM on the 5090 alongside everything else on
node-50. speaches stays up for clean-audio-mcp. To backfill older recordings,
run [`scripts/vibevoice-batch.py`](scripts/vibevoice-batch.py) from a machine
with cluster access:

```sh
kubectl -n ai-stack port-forward svc/ollama-5090 11434:11434 &
python3 apps/ai/riffado/scripts/vibevoice-batch.py              # all not yet on VibeVoice
python3 apps/ai/riffado/scripts/vibevoice-batch.py <id> [<id>]  # these, even if done
```

It transcribes each recording and re-runs every summary. The first start
downloads ~16 GiB of weights to the NVMe. Riffado lists these transcripts with
provider `browser` and model `vibevoice-asr`; the Re-transcribe button works
too.

**Bun idle timeout:** v0.6.4 runs on Bun, whose fetch drops a response that
sends nothing for 300 s, whatever `WHISPER_REQUEST_TIMEOUT_MS` says. VibeVoice
sends nothing until the transcript is done, so every request over ~5 min was
lost (the OpenAI SDK then retried into VibeVoice's HTTP 429). Summaries hit
the same cutoff. `BUN_CONFIG_HTTP_IDLE_TIMEOUT=21600` (seconds) in
`20-deployment.yaml` lifts it; verified on Bun 1.3.14 in the Riffado image
(2026-10-27).

Throughput on 2026-10-20, sharing the 5090: 16-57 min recordings took 2-7 min
each. Chunks where the model loops until it hits its output limit take ~15 min
before they are split and retried; 38 and 90 min recordings took 31 and 29 min.
Long recordings over-split speakers (11 labels for 90 min), because
cross-chunk matching only merges confident matches.

Riffado provider (one-time, set as the default transcription provider):
Custom, base URL `http://vibevoice.riffado.svc.cluster.local/v1`, model
**`vibevoice-asr`** exactly (a name containing `diarize` takes Riffado's OpenAI
diarization path). The API key is `VIBEVOICE_API_KEY` in `11-secret.sops.yaml`.

Requests are synchronous and one at a time (a second gets HTTP 429). Each loads
the model and exits, so idle VRAM is ~0. `WHISPER_REQUEST_TIMEOUT_MS` is 6 h to
match its processing cap. An interrupted run (pod restart, node-50 rebooting to
the desktop) starts over.

Manual uploads in v0.6.4 are cut off at 10 MiB by the Next.js middleware despite
the 500 MiB the UI advertises. Plaud sync downloads server-side and is
unaffected.

### Rebuilding the image

The image (~15 GB unpacked) is built on node-50 and imported into its
containerd, because `registry.internal.white.fm` has ~6 GB free. Run a
privileged `moby/buildkit` pod on node-50 with `/work` as a hostPath under
`/opt/local-path-provisioner/vibevoice-build` (the NVMe; the root disk is
small):

```
buildctl-daemonless.sh build --frontend=dockerfile.v0 \
  --opt context=https://github.com/swallace17/vibevoice-k8s.git#<commit> \
  --opt filename=Dockerfile --opt platform=linux/amd64 \
  --output type=docker,name=registry.internal.white.fm/vibevoice-k8s:<tag>,dest=/work/out/vibevoice.tar
```

Then on node-50:
`ctr -a /run/k3s/containerd/containerd.sock -n k8s.io images import vibevoice.tar`
(`ctr` lives under `/var/lib/rancher/rke2/data/*/bin/`), then pin it so kubelet
image GC leaves it alone whenever the Deployment is scaled to 0:
`ctr ... images label <image> io.cri-containerd.pinned=pinned`. Remove the
tarball and BuildKit state afterwards, and update the tag in
`54-vibevoice-deployment.yaml`. node-50's root disk was 89% full after the
first import (2026-10-20), and GC starts at 85%.
