// Combined Sonarr + Radarr transform for the shared Media webhook.
// Paste this into the transformation function field for the Media webhook
// in Element: Room Info → Extensions → <webhook> → Edit.
//
// Output format (compact, scannable when events arrive in bursts):
//   🎬 Sonarr · 🗑️ Episode File Deleted
//   The Bear (2022) — S03E04 "Violet"
//   1080p · qBittorrent · NZBgeek

result = (() => {
  const ev = data.eventType || "Unknown";

  const isSonarr = !!data.series;
  const isRadarr = !!data.movie;
  const app      = isSonarr ? "Sonarr" : isRadarr ? "Radarr" : "Media";

  const evLabels = {
    Grab:                      "📥 Grabbed",
    Download:                  "📬 Imported",
    Rename:                    "✏️ Renamed",
    SeriesAdd:                 "➕ Series Added",
    SeriesDelete:              "➖ Series Deleted",
    MovieAdded:                "➕ Movie Added",
    MovieDelete:               "➖ Movie Deleted",
    EpisodeFileDelete:         "🗑️ Episode File Deleted",
    MovieFileDelete:           "🗑️ Movie File Deleted",
    Health:                    "⚠️ Health Issue",
    HealthRestored:            "✅ Health Restored",
    ManualInteractionRequired: "⚠️ Manual Action Required",
    Test:                      "🔧 Test",
    ApplicationUpdate:         "🔄 App Updated",
  };
  const evLabel = evLabels[ev] || ev;

  // Build the title line: "<show/movie> (year) — S01E02 "Episode Title""
  let titleLine = "";
  if (isSonarr) {
    const s     = data.series.title || "Unknown series";
    const year  = data.series.year ? ` (${data.series.year})` : "";
    const eps   = Array.isArray(data.episodes) ? data.episodes : [];
    const codes = eps.map(e => {
      // Only emit a code if both fields are real numbers — never "S00E00".
      if (typeof e.seasonNumber !== "number" || typeof e.episodeNumber !== "number") return null;
      const sn = String(e.seasonNumber).padStart(2, "0");
      const en = String(e.episodeNumber).padStart(2, "0");
      return `S${sn}E${en}`;
    }).filter(Boolean);
    const epTitles = eps.map(e => e.title).filter(Boolean);
    const codePart  = codes.length    ? ` — ${codes.join(", ")}`           : "";
    const titlePart = epTitles.length ? ` "${epTitles.join(", ")}"`        : "";
    titleLine = `${s}${year}${codePart}${titlePart}`;
  } else if (isRadarr) {
    const m    = data.movie.title || "Unknown movie";
    const year = data.movie.year ? ` (${data.movie.year})` : "";
    titleLine = `${m}${year}`;
  }

  // Meta: quality · client · indexer — anything missing is dropped.
  const quality  = (data.release    && data.release.quality)    ||
                   (data.episodeFile && data.episodeFile.quality) ||
                   (data.movieFile   && data.movieFile.quality)   || "";
  const dlClient = data.downloadClient || "";
  const indexer  = (data.release && data.release.indexer) || "";
  const message  = data.message || "";
  const meta     = [quality, dlClient, indexer].filter(Boolean).join(" · ");

  // Plain text — three lines max.
  const plainLines = [`🎬 ${app} · ${evLabel}`];
  if (titleLine) plainLines.push(titleLine);
  if (message)   plainLines.push(message);
  if (meta)      plainLines.push(meta);
  const plain = plainLines.join("\n");

  // HTML — same shape, paragraphs/breaks instead of a table so it stays compact in Element.
  const esc = s => String(s).replace(/[&<>"']/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[c]);
  const htmlLines = [`<b>🎬 ${esc(app)}</b> · ${esc(evLabel)}`];
  if (titleLine) htmlLines.push(esc(titleLine));
  if (message)   htmlLines.push(`<i>${esc(message)}</i>`);
  if (meta)      htmlLines.push(`<small>${esc(meta)}</small>`);
  const html = htmlLines.join("<br>");

  return {
    version: "v2",
    empty:   false,
    plain,
    html,
    msgtype: "m.notice",
  };
})();
