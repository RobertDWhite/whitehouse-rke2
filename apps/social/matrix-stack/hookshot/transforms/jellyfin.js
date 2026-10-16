// Hookshot generic-webhook transformation function for the Jellyfin Webhook
// plugin (Generic destination, "Send All Properties" on).
//
// Installed on the "Jellyfin" hook in the Media room (!CqQCqfRZaQpzcQuYHa).
// The state event carries this source as transformationFunction; to change it,
// edit here and paste into Element (Room Info -> Extensions -> Webhook -> edit).

result = (() => {
  const ev = data.NotificationType || "Event";

  const esc = s => String(s).replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"
  })[c]);

  // "Show S01E02 - Title" for episodes, "Title (Year)" otherwise.
  const title = (() => {
    if (data.ItemType === "Episode" && data.SeriesName) {
      const code = (data.SeasonNumber00 && data.EpisodeNumber00)
        ? ` S${data.SeasonNumber00}E${data.EpisodeNumber00}` : "";
      return `${data.SeriesName}${code}${data.Name ? " - " + data.Name : ""}`;
    }
    if (data.ItemType === "Season" && data.SeriesName) return `${data.SeriesName} - ${data.Name || "Season"}`;
    return `${data.Name || "Unknown"}${data.Year ? ` (${data.Year})` : ""}`;
  })();

  const user   = data.NotificationUsername || data.Username || "";
  const device = [data.ClientName, data.DeviceName].filter(Boolean).join(" on ");

  // Ticks are 100ns units.
  const fmtTicks = t => {
    if (!t) return "";
    const s = Math.floor(Number(t) / 1e7);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h ? `${h}h${String(m).padStart(2, "0")}m` : `${m}m`;
  };
  const pct = (data.PlaybackPositionTicks && data.RunTimeTicks)
    ? Math.round(100 * Number(data.PlaybackPositionTicks) / Number(data.RunTimeTicks)) : null;

  let tag, head, detail = [];
  switch (ev) {
    case "ItemAdded":
      tag = "[added]"; head = `Added ${data.ItemType ? data.ItemType.toLowerCase() : "item"}: ${title}`;
      if (data.RunTimeTicks) detail.push(fmtTicks(data.RunTimeTicks));
      break;
    case "PlaybackStart":
      tag = "[play]"; head = `${user || "Someone"} started ${title}`;
      if (device) detail.push(device);
      break;
    case "PlaybackStop":
      tag = "[stop]"; head = `${user || "Someone"} stopped ${title}`;
      if (data.PlayedToCompletion === true || data.PlayedToCompletion === "True") detail.push("finished");
      else if (pct !== null) detail.push(`at ${pct}%`);
      if (device) detail.push(device);
      break;
    case "AuthenticationFailure":
      tag = "[auth-fail]"; head = `Failed login${user ? " for " + user : ""}`;
      if (data.RemoteEndPoint) detail.push(`from ${data.RemoteEndPoint}`);
      if (device) detail.push(device);
      break;
    case "UserLockedOut":
      tag = "[locked]"; head = `User locked out: ${user || "?"}`;
      break;
    case "UserCreated":
      tag = "[+user]"; head = `User created: ${user || "?"}`;
      break;
    case "PluginInstalled":
    case "PluginUpdated":
    case "PluginUninstalled":
      tag = "[plugin]"; head = `${ev.replace("Plugin", "Plugin ").toLowerCase()}: ${data.PluginName || "?"} ${data.PluginVersion || ""}`.trim();
      break;
    default:
      tag = "[event]"; head = `${ev}${data.Name ? ": " + title : ""}`;
  }

  const meta  = detail.filter(Boolean).join(" * ");
  const plain = `${tag} Jellyfin ${head}` + (meta ? `\n  ${meta}` : "");
  const html  = `<b>Jellyfin</b> ${esc(head)}` + (meta ? `<br><small>${esc(meta)}</small>` : "");

  return { version: "v2", empty: false, plain, html, msgtype: "m.notice" };
})();
