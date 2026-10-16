# clean-audio-mcp

Makes an **"English (Clean)"** audio track for movies/episodes, something like
a TV edit. Whisper (the `speaches` GPU instance in `riffado`) transcribes the
dialogue with word timestamps, profanity is matched against tiered word lists,
and only the dialogue is muted for each hit. Source: `RobertDWhite/clean-audio-mcp`.

The result is written beside the video as `<name>.en.Clean.mka` (E-AC-3,
~0.5 GB) plus a redacted `<name>.en.Clean.srt`. **Jellyfin** lists these as
extra "Clean" audio/subtitle tracks and pairs them with the video at playback
time; the video file is never modified. Plex can't use external audio
(`output=track` remuxes it into the file for Plex instead).

- Endpoint: `https://clean-audio-mcp.internal.white.fm/mcp` (tailnet, bearer `MCP_TOKEN`).
- Media: the Synology `Media` export is mounted at Plex's own paths
  (`/movies`, `/documentaries_movies`, `/tv`, `/documentaries_series`). Mount
  it at the same paths in Jellyfin so the refresh notification lines up. Runs
  as 1026:100 (the *arr PUID/PGID).
- State (`/state`, Longhorn): job history, per-job reports, cached transcripts.
  A re-run with different tiers skips Whisper.
- Notifications: finished jobs (written, dry run ready, failed, removed) are
  posted to the Matrix **Media** room through the hookshot generic webhook
  `clean-audio` (`NOTIFY_WEBHOOK_URL` in the secret, in-cluster hookshot URL).
  Rotate with `apps/social/matrix-stack/hookshot/rotate-webhook.sh`.
- Jellyfin refresh: optional `JELLYFIN_URL` / `JELLYFIN_API_KEY` keys in
  `11-secret.sops.yaml`. Without them, sidecars appear on the next library scan.

## Using it

```
clean_find "pulp fiction"
clean_start path=... dry_run=true          # transcribe + report only
clean_report job_id=...                    # hits, timestamps, context, unresolved subtitle cues
clean_start path=... dry_run=false         # write the sidecars (transcript cached)
clean_start path=... dry_run=false force=true tiers=[strong,slurs]   # rebuild with a different tier set
clean_remove path=...                      # video (deletes its sidecars) or an orphan sidecar
clean_list orphans_only=true
```

How each track is muted:

| Source | Method | Effect |
|--------|--------|--------|
| 5.1 / 7.1 | `center` | Only the centre channel is muted; music and effects play on. 7.1 is folded to 5.1 for E-AC-3. |
| Stereo | `midside` | The mix is swapped for its L−R side signal, which cancels centred dialogue. |
| Mono / other | `mute` | All channels are silenced. |

## Things to know

- Sidecars are named after the video. When Radarr/Sonarr **upgrades or
  renames** a file, the old sidecar is orphaned: `clean_list orphans_only=true`
  lists them; re-run `clean_start` on the new file and `clean_remove` the orphan.
- In Jellyfin, pick "Clean" once per title (or enable *Remember audio
  selections* in the user's playback settings). Both tracks are English, so the
  language preference can't choose it automatically.
- `output=track` rewrites the whole file through a hidden temp copy and needs
  free space equal to its size; the job refuses to start without it.
- Subtitle cross-check: if the English subtitles show more profanity in a cue
  than Whisper found, that window is re-transcribed with the cue text as the
  prompt. Anything still missing is listed under `unresolved_subtitle_cues`;
  mute those with `extra_windows`.
- Image: `registry.internal.white.fm/clean-audio-mcp` (built and pushed from
  the source repo with `docker buildx --platform linux/amd64,linux/arm64`).
