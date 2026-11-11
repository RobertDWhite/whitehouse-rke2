// Alertmanager security receiver. Source of truth for the security-soc hook.
result = (() => {
  const alerts = Array.isArray(data.alerts) ? data.alerts : [];
  const lines = alerts.map(a => {
    const l = a.labels || {}, n = a.annotations || {};
    const status = String(a.status || data.status || 'firing').toUpperCase();
    return `[${status}] ${l.severity || 'info'} / ${l.category || 'security'}\n` +
      `${n.summary || l.alertname || 'Security alert'}\n` +
      `${n.description || ''}\n` +
      `${l.mitre_attack ? 'MITRE ATT&CK: ' + l.mitre_attack + '\n' : ''}` +
      `${n.dashboard_url || 'https://grafana.internal.white.fm/d/security-soc/security-soc'}`;
  });
  return {version:'v2', plain: lines.join('\n\n') || 'Security SOC webhook received an empty alert group.'};
})();
