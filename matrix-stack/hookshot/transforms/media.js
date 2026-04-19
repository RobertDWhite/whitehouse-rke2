// Combined Sonarr + Radarr transform for the shared Media webhook.
// Paste this into the transformation function field for the Media webhook
// in Element: Room Info → Extensions → <webhook> → Edit.

result = (() => {
  const ev = data.eventType || "Unknown";

  const isSonarr = !!data.series;
  const isRadarr = !!data.movie;
  const app  = isSonarr ? "Sonarr" : isRadarr ? "Radarr" : "Unknown";
  const type = isSonarr ? "series"  : isRadarr ? "movie"  : "unknown";

  let title;
  if (isSonarr) {
    const s     = data.series.title || "Unknown";
    const year  = data.series.year  ? ` (${data.series.year})` : "";
    const eps   = Array.isArray(data.episodes) ? data.episodes : [];
    const codes = eps.map(e => {
      const sn = String(e.seasonNumber  ?? 0).padStart(2, "0");
      const en = String(e.episodeNumber ?? 0).padStart(2, "0");
      return `S${sn}E${en}`;
    });
    title = codes.length ? `${s}${year} — ${codes.join(", ")}` : `${s}${year}`;
  } else if (isRadarr) {
    const m    = data.movie.title || "Unknown";
    const year = data.movie.year  ? ` (${data.movie.year})` : "";
    title = `${m}${year}`;
  } else {
    title = ev;
  }

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

  const quality  = (data.release    && data.release.quality)    ||
                   (data.episodeFile && data.episodeFile.quality) ||
                   (data.movieFile   && data.movieFile.quality)   || "";
  const dlClient = data.downloadClient || "";
  const indexer  = (data.release && data.release.indexer) || "";
  const message  = data.message || "";

  const rows = [
    ["Event",  evLabel],
    ["Title",  title],
    ["Type",   type],
    ["App",    app],
  ];
  if (dlClient) rows.push(["Client",  dlClient]);
  if (quality)  rows.push(["Quality", quality]);
  if (indexer)  rows.push(["Indexer", indexer]);
  if (message)  rows.push(["Status",  message]);

  const plain = "🎬 Media\n" + rows.map(([k, v]) => `${k}\t${v}`).join("\n");

  const esc = s => String(s).replace(/[&<>"']/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[c]);
  const htmlRows = rows.map(([k, v]) =>
    `<tr><td><b>${esc(k)}</b></td><td>${esc(v)}</td></tr>`).join("");
  const html = `<p><b>🎬 Media</b></p><table>${htmlRows}</table>`;

  return {
    version: "v2",
    empty:   false,
    plain,
    html,
    msgtype: "m.notice",
  };
})();
