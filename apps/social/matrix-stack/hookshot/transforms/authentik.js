// Authentik notification webhook transform for Hookshot.
//
// Authentik's generic webhook transport posts:
//   { body, severity, user_email, user_username }
// plus optional { event_user_email, event_user_username } when the notification
// originated from an Event (login_failed, policy_exception, suspicious_request, …).
//
// A custom NotificationWebhookMapping in Authentik can override this shape;
// any unrecognized payload falls through to a JSON dump so it stays readable.

result = (() => {
  const esc = s => String(s).replace(/[&<>"']/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[c]);

  if (data && (typeof data.body === "string") && (typeof data.severity === "string")) {
    const sev = data.severity.toLowerCase();
    const icon = sev === "alert"   ? "🚨"
               : sev === "warning" ? "⚠️"
               : sev === "notice"  ? "🔔"
               :                     "ℹ️";

    const sub  = data.user_username || data.user_email || "";
    const trig = data.event_user_username || data.event_user_email || "";

    const headerPlain = `${icon} Authentik · ${data.severity}`;
    const headerHtml  = `<b>${icon} Authentik</b> <small>${esc(data.severity)}</small>`;

    const lines = [headerPlain, data.body];
    const htmlLines = [headerHtml, esc(data.body).replace(/\n/g, "<br>")];

    const meta = [];
    if (sub)  meta.push(`for ${sub}`);
    if (trig && trig !== sub) meta.push(`triggered by ${trig}`);
    if (meta.length) {
      lines.push(meta.join(" · "));
      htmlLines.push(`<small>${esc(meta.join(" · "))}</small>`);
    }

    return {
      version: "v2",
      empty:   false,
      plain:   lines.join("\n"),
      html:    htmlLines.join("<br>"),
      msgtype: "m.notice",
    };
  }

  // Custom mapping or unknown shape — dump it.
  const dump = JSON.stringify(data, null, 2);
  return {
    version: "v2",
    empty:   false,
    plain:   `🛡️ Authentik (raw)\n${dump}`,
    html:    `<b>🛡️ Authentik (raw)</b><pre><code>${esc(dump)}</code></pre>`,
    msgtype: "m.notice",
  };
})();
