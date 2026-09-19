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
