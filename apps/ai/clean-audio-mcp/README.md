# clean-audio-mcp

Adds an **"English (Clean)"** audio track to movies/episodes, something like a
TV edit. Whisper (the `speaches` GPU instance in `riffado`) transcribes the
dialogue with word timestamps, profanity is matched against tiered word lists,
and only the dialogue is muted for each hit. The new track is remuxed into the
file as a non-default E-AC-3 stream, so Plex lists it next to the original
audio. Source: `RobertDWhite/clean-audio-mcp`.

- Endpoint: `https://clean-audio-mcp.internal.white.fm/mcp` (tailnet, bearer `MCP_TOKEN`).
- Media: the Synology `Media` export is mounted at Plex's own paths
  (`/movies`, `/documentaries_movies`, `/tv`, `/documentaries_series`), so a
  partial Plex refresh after each remux lines up. Runs as 1026:100 (the *arr PUID/PGID).
- State (`/state`, Longhorn): job history, per-job reports, cached transcripts.
  A re-run with different tiers skips Whisper.

## Why a remux and not a sidecar

Plex has no support for external audio files, and it does not read `.nfo`
files (Radarr writes them for Kodi/Jellyfin). The other route is a separate
`{edition-TV Version}` file, but that duplicates the whole video. Remuxing
copies the video bit-for-bit and adds one ~500 MB track. `clean_remove`
reverses it.

## Using it

```
clean_find "pulp fiction"
clean_start path=... dry_run=true          # transcribe + report only
clean_report job_id=...                    # hits, timestamps, context, unresolved subtitle cues
clean_start path=... dry_run=false         # write the track (transcript cached)
clean_start path=... dry_run=false force=true tiers=[strong,slurs]   # rebuild with a different tier set
clean_remove path=...
```

How each track is muted:

| Source | Method | Effect |
|--------|--------|--------|
| 5.1 / 7.1 | `center` | Only the centre channel is muted; music and effects play on. 7.1 is folded to 5.1 for E-AC-3. |
| Stereo | `midside` | The mix is swapped for its L−R side signal, which cancels centred dialogue. |
| Mono / other | `mute` | All channels are silenced. |

## Things to know

- The remux writes a hidden temp file beside the source and needs free space
  equal to the file size. The job refuses to start without it, and the NAS is
  near full.
- Radarr sees a changed file on its next rescan and does not replace it. An
  **upgrade** download would replace it with a file that has no clean track,
  so re-run the job after upgrades.
- Subtitle cross-check: if the English subtitles show more profanity in a cue
  than Whisper found, that window is re-transcribed with the cue text as the
  prompt. Anything still missing is listed under `unresolved_subtitle_cues`;
  mute those with `extra_windows`.
- Image: `registry.internal.white.fm/clean-audio-mcp` (built and pushed from
  the source repo with `docker buildx --platform linux/amd64,linux/arm64`).
