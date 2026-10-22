// Hookshot generic-webhook transformation function for the brain repo.
//
// Installed on the "brain" hook in the Brain room (!izMvHnoMYeLtxUakoz, E2EE).
// The state event carries this source as transformationFunction; to change it,
// edit here and paste into Element (Room Info -> Extensions -> Webhook -> edit).
//
// Handles two payload shapes:
//   1. Forgejo push webhook on WhiteHouse/brain — { ref, commits: [ { message,
//        added, modified, removed, ... } ], total_commits, compare_url, ... }.
//      Files are grouped by folder (two levels for Entities/ and Meerkat/,
//      otherwise one) and listed by note name.
//   2. Pre-rendered message (brain-briefing) — { text, html? }.

result = (() => {
  const esc = s => String(s).replace(/[&<>"']/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[c]);

  // ---- 2. pre-rendered message ---------------------------------------------
  if (data && typeof data.text === "string" && !data.commits) {
    const out = { version: "v2", empty: false, plain: data.text, msgtype: "m.text" };
    if (typeof data.html === "string") out.html = data.html;
    return out;
  }

  // ---- 1. Forgejo push -----------------------------------------------------
  if (data && Array.isArray(data.commits) && data.ref) {
    const branch = String(data.ref).replace(/^refs\/heads\//, "");
    if (!data.commits.length) return { version: "v2", empty: true };

    const MAX_NAMES = 8;
    const repoUrl = (data.repository && data.repository.html_url) || "";
    const who = [...new Set(data.commits.map(c =>
      (c.author && (c.author.username || c.author.name)) || "?"))].join(", ");
    const total = data.total_commits || data.commits.length;

    // Net change per path across the push, in commit order.
    const state = {};
    for (const c of data.commits) {
      for (const p of c.added || [])    state[p] = state[p] === "removed" ? "modified" : "added";
      for (const p of c.modified || []) state[p] = state[p] === "added" ? "added" : "modified";
      for (const p of c.removed || [])  state[p] = state[p] === "added" ? null : "removed";
    }

    const groupOf = p => {
      const parts = p.split("/");
      if (parts.length > 2 && (parts[0] === "Entities" || parts[0] === "Meerkat")) return parts[0] + "/" + parts[1];
      return parts.length > 1 ? parts[0] : "/";
    };
    const nameOf = p => p.split("/").pop().replace(/\.md$/, "");
    const mark = { added: "+", modified: "~", removed: "−" };

    const groups = {};
    for (const [p, s] of Object.entries(state)) {
      if (!s) continue;
      (groups[groupOf(p)] = groups[groupOf(p)] || []).push([p, s]);
    }
    const keys = Object.keys(groups).sort();

    const head = `${total} commit${total === 1 ? "" : "s"} by ${who}` + (branch !== "main" ? ` on ${branch}` : "");
    const plain = [`🧠 ${head}`];
    const html = [`<b>🧠 ${esc(head)}</b>` + (data.compare_url ? ` · <a href="${esc(data.compare_url)}">diff</a>` : "")];

    // Commit subjects (brain-sync's say what changed, e.g. "entities: 3 updated").
    const subjects = data.commits.map(c => String(c.message || "").split("\n")[0]).filter(Boolean);
    const shown = subjects.slice(0, 5);
    if (subjects.length > shown.length) shown.push(`… ${subjects.length - shown.length} more`);
    plain.push(...shown.map(s => `  ${s}`));
    html.push("<ul>" + shown.map(s => `<li>${esc(s)}</li>`).join("") + "</ul>");

    for (const k of keys) {
      const items = groups[k].sort((a, b) => nameOf(a[0]).localeCompare(nameOf(b[0])));
      const counts = ["added", "modified", "removed"]
        .map(s => [s, items.filter(i => i[1] === s).length]).filter(x => x[1])
        .map(([s, n]) => `${n} ${s}`).join(", ");
      const top = items.slice(0, MAX_NAMES);
      const more = items.length - top.length;
      plain.push(`▸ ${k} (${counts}): ` + top.map(([p, s]) => mark[s] + nameOf(p)).join(", ") +
                 (more ? `, +${more} more` : ""));
      const link = ([p, s]) => s !== "removed" && repoUrl
        ? `${mark[s]}<a href="${esc(repoUrl + "/src/branch/" + encodeURIComponent(branch) + "/" +
            p.split("/").map(encodeURIComponent).join("/"))}">${esc(nameOf(p))}</a>`
        : `${mark[s]}${esc(nameOf(p))}`;
      html.push(`<b>▸ ${esc(k)}</b> <small>(${esc(counts)})</small>: ` + top.map(link).join(", ") +
                (more ? `, +${more} more` : "") + "<br>");
    }

    return { version: "v2", empty: false, plain: plain.join("\n"), html: html.join(""), msgtype: "m.notice" };
  }

  // ---- unknown — dump it so we can see what it is ---------------------------
  const dump = JSON.stringify(data, null, 2);
  return {
    version: "v2", empty: false,
    plain: `🧠 unknown payload\n${dump}`,
    html: `<b>🧠 unknown payload</b><pre><code>${esc(dump)}</code></pre>`,
    msgtype: "m.notice",
  };
})();
