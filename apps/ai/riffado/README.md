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
| `vibevoice` | Deployment, GPU on rke2-node-50, **0 replicas** | VibeVoice-ASR with speaker labels, scaled up by hand for a transcription. See below. |
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

speaches returns plain text. For the occasional recording where who said what
matters, `vibevoice` runs Microsoft's
[VibeVoice-ASR](https://huggingface.co/microsoft/VibeVoice-ASR) through
[swallace17/vibevoice-k8s](https://github.com/swallace17/vibevoice-k8s) at
`f5fb110`. It returns `[hh:mm:ss] Speaker N` blocks in the transcript text. It
splits recordings over 35 minutes near pauses and matches voices across the
chunks. Speaker numbers are anonymous and apply to one recording only.

It is **off by default** (Argo ignores its replicas). Its model peaks at
~21.5 GiB of VRAM on the 5090 alongside everything else on node-50. Plaud sync
keeps going to speaches. To transcribe a recording with it:

1. `kubectl -n riffado scale deployment/vibevoice --replicas=1` and wait for
   Ready. The `unload-ollama` init container evicts ollama-5090's resident
   model first. The first start downloads ~16 GiB of weights to the NVMe.
2. In Riffado, set **VibeVoice** as the default transcription provider, click
   **Re-transcribe** on the recording, then set speaches back as default.
   v0.6.4's re-transcribe API takes a `providerId`, but it needs a browser
   session and the UI has no picker.
3. `kubectl -n riffado scale deployment/vibevoice --replicas=0`.

Riffado provider (one-time): Custom, base URL
`http://vibevoice.riffado.svc.cluster.local/v1`, model **`vibevoice-asr`**
exactly (a name containing `diarize` takes Riffado's OpenAI diarization path).
The API key is `VIBEVOICE_API_KEY` in `11-secret.sops.yaml`.

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
image GC leaves it alone while the Deployment is at 0 replicas:
`ctr ... images label <image> io.cri-containerd.pinned=pinned`. Remove the
tarball and BuildKit state afterwards, and update the tag in
`54-vibevoice-deployment.yaml`. node-50's root disk was 89% full after the
first import (2026-10-20), and GC starts at 85%.
