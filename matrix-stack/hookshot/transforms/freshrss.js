// FreshRSS webhook transform for Hookshot.
// Handles both individual-entry notifications (FreshRSS core webhook) and
// plain-text digest payloads from the daily-digest CronJob.
// Paste into the transformation function field for the FreshRSS webhook
// in Element: Room Info → Extensions → <webhook> → Edit.

result = (() => {
  // Plain-text digest from daily-digest CronJob: {"text": "..."}
  if (data.text && !data.entry && !data.entries && !data.item && !data.items) {
    return {
      version: "v2",
      empty:   false,
      plain:   String(data.text),
      msgtype: "m.text",
    };
  }

  // Extract the entry object — try the field paths FreshRSS core webhook uses
  const entry =
    data.entry                                                    ||
    data.item                                                     ||
    (Array.isArray(data.entries) && data.entries[0])              ||
    (Array.isArray(data.items)   && data.items[0])                ||
    data;  // last resort: fields may be at top level

  const title  = entry.title  || data.title  || "(no title)";
  const feed   =
    (entry.feed   && entry.feed.title)   ||
    (entry.origin && entry.origin.title) ||
    data.feed_title                      ||
    entry.feed_title                     ||
    "(unknown feed)";
  const url    =
    entry.url  || entry.link             ||
    (entry.canonical && entry.canonical[0] && entry.canonical[0].href) ||
    data.url   || data.link              || "";
  const author = entry.author || data.author || "";
  const cats   = Array.isArray(entry.categories)
    ? entry.categories.map(c => c.title || c).filter(Boolean).join(", ")
    : "";

  const rows = [
    ["Feed",  feed],
    ["Title", title],
  ];
  if (author) rows.push(["Author",     author]);
  if (cats)   rows.push(["Categories", cats]);
  if (url)    rows.push(["Link",       url]);

  const plain = "📰 FreshRSS\n" + rows.map(([k, v]) => `${k}\t${v}`).join("\n");

  const esc = s => String(s).replace(/[&<>"']/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[c]);
  const htmlRows = rows.map(([k, v]) =>
    `<tr><td><b>${esc(k)}</b></td><td>${esc(v)}</td></tr>`).join("");
  const html = `<p><b>📰 FreshRSS</b></p><table>${htmlRows}</table>`;

  return {
    version: "v2",
    empty:   false,
    plain,
    html,
    msgtype: "m.notice",
  };
})();
