// Hookshot generic-webhook transformation function for Sonarr v3+ webhooks.
//
// Install: paste this entire snippet into the "Transformation Function"
// field of the Sonarr webhook in Element (Room Info -> Extensions ->
// Webhook -> edit). Hookshot's `allowJsTransformationFunctions` is already
// enabled in hookshot-configmap.yaml.
//
// Replaces the previous transform whose title was the literal string
// "Sonarr event - S??E??" because the SxxExx code was hard-coded instead
// of being built from data.episodes[].seasonNumber / .episodeNumber.

result = (() => {
  const ev     = data.eventType || "Event";
  const series = (data.series && data.series.title) || "Unknown series";
  const year   = (data.series && data.series.year)  ? ` (${data.series.year})` : "";

  const eps = Array.isArray(data.episodes) ? data.episodes : [];
  const codes = eps.map(e => {
    const s = String(e.seasonNumber  ?? 0).padStart(2, "0");
    const n = String(e.episodeNumber ?? 0).padStart(2, "0");
    return `S${s}E${n}`;
  });
  const epTitles = eps.map(e => e.title).filter(Boolean);

  const quality  = (data.release && data.release.quality)  || "";
  const indexer  = (data.release && data.release.indexer)  || "";
  const dlClient = data.downloadClient || data.downloadClientType || "";

  const fmtSize = b => {
    if (!b) return "";
    const u = ["B","KB","MB","GB","TB"];
    let i = 0, n = b;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(1)} ${u[i]}`;
  };
  const size = fmtSize(data.release && data.release.size);

  const icons = {
    Grab:              "[grab]",
    Download:          "[dl]",
    Rename:            "[rename]",
    SeriesAdd:         "[+series]",
    SeriesDelete:      "[-series]",
    EpisodeFileDelete: "[-file]",
    Health:            "[health]",
    HealthRestored:    "[health-ok]",
    Test:              "[test]",
    ApplicationUpdate: "[update]",
  };
  const tag  = icons[ev] || "[event]";
  const verb = ev === "Grab" ? "Grabbed" : ev;

  const epPart    = codes.length    ? codes.join(", ")      : "";
  const titlePart = epTitles.length ? ` - ${epTitles.join(", ")}` : "";
  const meta      = [quality, size, dlClient].filter(Boolean).join(" * ");

  // Plain text
  const plainLines = [`${tag} Sonarr ${verb}: ${series}${year}`];
  if (epPart) plainLines.push(`  ${epPart}${titlePart}`);
  if (meta)   plainLines.push(`  ${meta}`);
  if (indexer) plainLines.push(`  via ${indexer}`);
  const plain = plainLines.join("\n");

  // HTML
  const esc = s => String(s).replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"
  })[c]);
  const htmlLines = [`<b>Sonarr ${esc(verb)}:</b> ${esc(series)}${esc(year)}`];
  if (epPart) {
    const right = titlePart ? ` - <i>${esc(epTitles.join(", "))}</i>` : "";
    htmlLines.push(`<code>${esc(epPart)}</code>${right}`);
  }
  if (meta)    htmlLines.push(`<small>${esc(meta)}</small>`);
  if (indexer) htmlLines.push(`<small>via ${esc(indexer)}</small>`);
  const html = htmlLines.join("<br>");

  return {
    version: "v2",
    empty:   false,
    plain:   plain,
    html:    html,
    msgtype: "m.notice",
  };
})();
