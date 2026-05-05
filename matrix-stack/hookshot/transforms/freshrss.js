// FreshRSS / BI-pipeline webhook transform for Hookshot.
//
// Handles three payload shapes that all hit the FreshRSS hook:
//   1. bi-pipeline alert digest — { run_at, total_alerts, watchlist_total,
//        coverage_gaps, top: { customer: [ { article_title, url, source,
//        score, event_type, ... }, ... ] } }
//   2. plain text digest — { text: "..." } (from daily-digest CronJob)
//   3. feed entry notification — { entry|item|entries|items: { title, url, ... } }
//
// Anything we don't recognize falls back to a JSON dump so it's still readable.

result = (() => {
  const esc = s => String(s).replace(/[&<>"']/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[c]);

  // ---- 1. bi-pipeline alert digest ------------------------------------------
  if (data && (data.top || data.customer_alerts) &&
      (typeof data.total_alerts === "number" || data.run_at)) {
    const top   = data.top || data.customer_alerts || {};
    const total = data.total_alerts ?? 0;
    const wl    = data.watchlist_total ?? 0;
    const gaps  = Array.isArray(data.coverage_gaps) ? data.coverage_gaps.length : 0;
    const ts    = data.run_at ? String(data.run_at).replace("T", " ").replace(/\..*$/, "") : "";

    const summary = [
      `${total} alert${total === 1 ? "" : "s"}`,
      `${wl} watchlist`,
      `${gaps} coverage gap${gaps === 1 ? "" : "s"}`,
    ].join(" · ");

    const customers = Object.keys(top).sort();
    const plainParts = [`📰 BI Run${ts ? ` (${ts})` : ""} · ${summary}`];
    const htmlParts  = [`<b>📰 BI Run</b>${ts ? ` <small>${esc(ts)}</small>` : ""} · ${esc(summary)}`];

    if (customers.length === 0) {
      plainParts.push("(no customer alerts this run)");
      htmlParts.push("<i>no customer alerts this run</i>");
    } else {
      for (const c of customers) {
        const items = Array.isArray(top[c]) ? top[c] : [];
        if (!items.length) continue;
        plainParts.push("");
        plainParts.push(`▸ ${c}`);
        htmlParts.push(`<br><b>▸ ${esc(c)}</b><ul>`);
        for (const a of items) {
          const title  = a.article_title || a.title || "(untitled)";
          const url    = a.url || a.link || "";
          const src    = a.source_name || a.source || "";
          const score  = (typeof a.score === "number") ? Math.round(a.score) : null;
          const ev     = a.event_type || "";
          const meta   = [
            score !== null ? `score ${score}` : "",
            ev,
            src,
          ].filter(Boolean).join(" · ");

          plainParts.push(`  • ${title}${meta ? `  [${meta}]` : ""}${url ? `\n    ${url}` : ""}`);
          const linkHtml = url ? `<a href="${esc(url)}">${esc(title)}</a>` : esc(title);
          htmlParts.push(`<li>${linkHtml}${meta ? ` <small>[${esc(meta)}]</small>` : ""}</li>`);
        }
        htmlParts.push("</ul>");
      }
    }

    return {
      version: "v2",
      empty:   false,
      plain:   plainParts.join("\n"),
      html:    htmlParts.join(""),
      msgtype: "m.notice",
    };
  }

  // ---- 2. plain-text digest -------------------------------------------------
  if (data && data.text && !data.entry && !data.entries && !data.item && !data.items) {
    return {
      version: "v2",
      empty:   false,
      plain:   String(data.text),
      msgtype: "m.text",
    };
  }

  // ---- 3. single feed entry -------------------------------------------------
  const entry =
    data.entry                                                    ||
    data.item                                                     ||
    (Array.isArray(data.entries) && data.entries[0])              ||
    (Array.isArray(data.items)   && data.items[0])                ||
    null;

  if (entry) {
    const title  = entry.title || data.title || "(no title)";
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

    const plainLines = [
      `📰 ${feed}`,
      url ? `${title}\n${url}` : title,
    ];
    if (author) plainLines.push(`— ${author}`);

    const titleHtml = url ? `<a href="${esc(url)}">${esc(title)}</a>` : esc(title);
    const htmlLines = [
      `<b>📰 ${esc(feed)}</b>`,
      titleHtml,
    ];
    if (author) htmlLines.push(`<small>— ${esc(author)}</small>`);

    return {
      version: "v2",
      empty:   false,
      plain:   plainLines.join("\n"),
      html:    htmlLines.join("<br>"),
      msgtype: "m.notice",
    };
  }

  // ---- 4. unknown — dump it so we can see what it is -------------------------
  const dump = JSON.stringify(data, null, 2);
  return {
    version: "v2",
    empty:   false,
    plain:   `📰 unknown payload\n${dump}`,
    html:    `<b>📰 unknown payload</b><pre><code>${esc(dump)}</code></pre>`,
    msgtype: "m.notice",
  };
})();
