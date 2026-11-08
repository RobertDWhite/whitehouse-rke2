# Home videos

A browsable, searchable archive of the family's digitised VHS tapes at
https://home-videos.pages.internal.white.fm/ (internal network only), in the
same style as the Cards archive. The tapes stay on the Synology
(`/volume1/Media/VHS_Conversions/<family>/`). The site streams them from there
and seeks to chapters and transcript lines.

## Pieces

| Piece | Where | What it does |
| --- | --- | --- |
| `vhs` PV/PVC | NFS `10.100.1.20:/volume1/Media/VHS_Conversions`, read-only | The tapes. Nothing here writes to the NAS. |
| `work` PVC | Longhorn 30 Gi | Indexer output: `White/<slug>/{meta,tx,captions,info}.json`, `thumbs/`, `audio.flac`. |
| `home-videos-indexer` Job | `40-job-indexer.yaml`, script `indexer/indexer.py` | Frames, audio, transcription, captions, classification. Resumable. |
| `home-videos-retime` Job | `42-job-retime.yaml`, `indexer.py retime` | Re-transcribes spans that came back without word timings, in short pieces. |
| `home-videos-voices` Job | `44-job-voices.yaml`, script `indexer/voices.py` | Voice fingerprints per tape, grouped into voices, with AI name guesses: `voices.json`. |
| `labels` | `35-labels.yaml`, `labels/server.py` | Who each voice is, served at `/api/labels` on the site's host. Own 1 Gi volume; every change is appended to `history.jsonl`. |
| `media-server` | nginx, `30-media-server.yaml` | Serves the tapes read-only at `/media/` on the site's host (route `apps/ai/pages/58-httproute-home-videos.yaml`). H.264/AAC `.mov` plays natively in browsers when served as `video/mp4`, and Range requests make seeking work. |
| Site | `site/build_site.py` + `site/template.html`, `site/label.html` | Static Pages site: `index.html` with the catalog embedded, `label.html` (name the voices), `t/<slug>/<k>.jpg` thumbnails, `data/<slug>.json` transcripts and captions, `data/voices.json`. |
| Brain notes | `Home Videos/.import/build.py` in the brain repo | brain-imports turns the site into `Home Videos/Tapes/*` (summary, chapters with video links, who speaks) and `Home Videos/Transcripts/*` every 30 min. Transcripts are kept out of the knowledge graph (`EXCLUDE_GLOBS` in `apps/ai/graph`). |

## Who's who

Family names and relationships for the prompts live in `12-people.sops.yaml`
(Secret `home-videos-people`, encrypted because this repo is public): `people.md`
(free-text notes for the classifier and the voice guesser) and `names.json` (the
exact names offered for people and speakers). The Jobs copy `people.md` to
`/work/White/people.md`; the labels API mounts `names.json`. Edit with
`sops apps/media/home-videos/12-people.sops.yaml`, then re-run
`41-job-reclassify.yaml`.

## The indexer

For each tape (exact duplicates such as `X (1).mov` are folded in, and a ProRes
master is replaced by its `.low` H.264 copy), shortest first, four workers run
concurrently:

1. **extract**: a frame every 30 s (320 px for the site, 640 px for captioning)
   and 16 kHz mono FLAC audio, using ffmpeg.
2. **transcribe**: the FLAC goes to `nemotron-transcribe` (riffado namespace, the
   5090). That gives speaker-labelled segments with word timings. Tapes run one
   at a time at roughly 5x realtime once vLLM is warm. Expect about 15 h for the
   White folder's 78 h. While a tape is running, ollama-5090 is unloaded and new
   Riffado recordings wait their turn.
3. **caption**: every 640 px frame goes to qwen-flash (GB10, `qwen3.6-35b-a3b`,
   vision). It describes the scene and reads the camcorder date stamp, about
   1 s per frame with 4 in flight.
4. **classify**: qwen-flash reads the frame timeline and the transcript. It
   returns title, summary, date range, categories, people, places, chapters and
   highlights in `info.json`. If `/work/White/people.md` exists, its who-is-who
   notes go into the prompt.

```sh
# (re)start: picks up where the last run stopped
kubectl -n home-videos delete job home-videos-indexer --ignore-not-found
kubectl create -f apps/media/home-videos/40-job-indexer.yaml
kubectl -n home-videos logs -f job/home-videos-indexer

# re-classify everything (e.g. after editing the who's-who): 41-job-reclassify.yaml
kubectl -n home-videos exec job/home-videos-indexer -- sh -c 'rm /work/White/*/info.json'
#   then the Job with args: [classify]
```

Another family folder (McGuire, Stamper, Wallace) works the same way: run the Job
with `SRC=/vhs/<Folder>` and `WORK=/work/<Folder>`. The site builder only knows
`White` for now.

## Fixing timings: retime

nemotron-transcribe sends the model chunks of up to ~29 minutes. On noisy VHS audio
the CTC aligner often fails on a whole chunk (`ctc timestamps unavailable:
alignment_failed` in its log), and every segment of that chunk then spans the
whole chunk, so transcript lines can't be synced or played. The first pass lost
timings on 104 chunks, about 43 of the 78 hours. `retime` finds those spans and
re-transcribes them in pieces of 2.5 to 4 minutes cut at pauses (60 s pieces if a
piece still fails), which takes seconds per piece. It keeps the original as
`tx.orig.json` and re-classifies the tapes it changed (the old `info.json` is kept
as `info.pre-retime.json`).

```sh
kubectl create -f apps/media/home-videos/42-job-retime.yaml
```

The adapter now also caps output tokens per second of audio, so the model can't
loop on music or tape hiss until it fills the context (`MAX_TOKENS_PER_SECOND`
in `apps/ai/riffado/nemotron/adapter.py`).

## Speakers

Nemotron labels speakers per request (up to four, "Speaker 1".."Speaker 4"), so
"Speaker 1" in two pieces of a tape can be two people, and it knows no names.
`voices.py` (Job `44-job-voices.yaml`, CPU, ~1 h for everything):

1. Groups transcript segments by request and Nemotron speaker.
2. Embeds each group's longest utterances with SpeechBrain's ECAPA speaker model.
3. Merges groups with similar voices into the tape's voices `v1`, `v2`, ... (most
   speech first), never merging two speakers Nemotron separated in one request.
4. Asks qwen-flash for a first guess at each voice's name, from the transcript and
   `people.md`.

Name the voices at https://home-videos.pages.internal.white.fm/label.html: play
the sample clips, pick a name (keys `1`-`0`), accept a suggestion (`a`), or jump
to the next unnamed voice (`n`). Voice matching across tapes suggests names you
have already given elsewhere, and "Same voice on other tapes" names several at
once. Names save to the labels API immediately and show up in the site's
transcripts and, within 30 minutes, in the brain notes. Re-running `voices.py`
renumbers a tape's voices (and orphans its labels), so it skips tapes whose
`voices.json` is newer than `tx.json`.

## Building the site

From the Mac (needs kubectl and sops). Pull the indexer output from the running
indexer pod, build, and upload to pages-mcp in batches of about 2.5 MB (the MCP
endpoint returns 413 on larger bodies):

```sh
python3 apps/media/home-videos/site/build_site.py             # pull + build + deploy
python3 apps/media/home-videos/site/build_site.py --no-pull   # rebuild from ~/.cache/home-videos
```

Tapes that are still in progress show up with an "indexing…" badge and play
already. Re-run the builder to pick up new results; labels don't need a rebuild.

Network access: `20-networkpolicy.yaml` opens nemotron-transcribe (riffado) to pods
labelled `app: home-videos-indexer`. qwen-flash is already open to 10.0.0.0/8. The
labels API has no auth: like the rest of the site, it is internal-network only.
