// Uptime Kuma webhook notification transform for Hookshot.
// UK posts:
//   { heartbeat: { status, time, msg, important, duration, ... },
//     monitor:   { id, name, url, type, hostname, port, ... },
//     msg:       "[name] [Up/Down] message" }
// status: 0=DOWN, 1=UP, 2=PENDING, 3=MAINTENANCE.
// "Test notification" payloads come through with no heartbeat — covered as a fallback.

result = (() => {
  const esc = s => String(s).replace(/[&<>"']/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[c]);

  const hb  = data && data.heartbeat ? data.heartbeat : null;
  const mon = data && data.monitor   ? data.monitor   : {};

  const fmtDur = s => {
    if (typeof s !== "number" || !isFinite(s) || s <= 0) return "";
    if (s < 60)    return `${Math.round(s)}s`;
    if (s < 3600)  return `${Math.round(s / 60)}m`;
    if (s < 86400) return `${(s / 3600).toFixed(1)}h`;
    return `${Math.round(s / 86400)}d`;
  };

  if (hb) {
    // --- noise control (fix/notification-noise) ---
    // Uptime Kuma fires on every state change. With the widened flap thresholds
    // the residual Status-room spam is (1) UP-recovery + PENDING-retry pings and
    // (2) a handful of monitors that flap on transient cluster blips, not real
    // outages. Drop both so only genuine DOWN/MAINTENANCE reaches Matrix; live
    // state is always on the Kuma dashboard. Revert = delete this block.
    // NOTE: this file is source-of-truth only. Hookshot reads the transform from
    // the room's `uk.half-shot.matrix-hookshot.generic.hook` state event (state_key
    // "Status"), so after editing you must push it into that state event for it to
    // take effect — editing this file alone changes nothing live.
    const MUTED = new Set(["Weather API", "LazyLibrarian", "Media MCP", "Prowlarr"]);
    if (hb.status === 1 || hb.status === 2 || MUTED.has(mon.name)) {
      return { version: "v2", empty: true };
    }

    const states = {
      0: { word: "DOWN",        icon: "🔴" },
      1: { word: "UP",          icon: "✅" },
      2: { word: "PENDING",     icon: "🟡" },
      3: { word: "MAINTENANCE", icon: "🛠️" },
    };
    const st   = states[hb.status] || { word: String(hb.status ?? "?"), icon: "❔" };
    const name = mon.name || "(unknown monitor)";
    const msg  = hb.msg || "";
    const dur  = fmtDur(hb.duration);

    // type-specific target descriptor (host:port / url / hostname)
    let target = "";
    if (mon.type === "port" && mon.hostname && mon.port) target = `${mon.hostname}:${mon.port}`;
    else if (mon.url)      target = mon.url;
    else if (mon.hostname) target = mon.hostname;

    const meta = [mon.type, target, dur ? `for ${dur}` : ""].filter(Boolean).join(" · ");

    const plainLines = [`${st.icon} ${name} · ${st.word}`];
    if (msg)  plainLines.push(msg);
    if (meta) plainLines.push(meta);

    const htmlLines = [`<b>${st.icon} ${esc(name)}</b> · ${esc(st.word)}`];
    if (msg)  htmlLines.push(esc(msg));
    if (meta) htmlLines.push(`<small>${esc(meta)}</small>`);

    return {
      version: "v2",
      empty:   false,
      plain:   plainLines.join("\n"),
      html:    htmlLines.join("<br>"),
      msgtype: "m.notice",
    };
  }

  // No heartbeat → likely the "Test" payload UK fires from the notification dialog.
  if (data && typeof data.msg === "string") {
    return {
      version: "v2",
      empty:   false,
      plain:   `🔔 Uptime Kuma\n${data.msg}`,
      html:    `<b>🔔 Uptime Kuma</b><br>${esc(data.msg)}`,
      msgtype: "m.notice",
    };
  }

  // Unknown shape — dump it.
  const dump = JSON.stringify(data, null, 2);
  return {
    version: "v2",
    empty:   false,
    plain:   `🔔 Uptime Kuma (raw)\n${dump}`,
    html:    `<b>🔔 Uptime Kuma (raw)</b><pre><code>${esc(dump)}</code></pre>`,
    msgtype: "m.notice",
  };
})();
