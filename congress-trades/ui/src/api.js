const BASE = '/api'

async function get(path, params) {
  const url = new URL(BASE + path, window.location.origin)
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v != null && v !== '') url.searchParams.set(k, v)
    }
  }
  const r = await fetch(url)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json()
}

async function send(method, path, body) {
  const r = await fetch(BASE + path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json()
}

export const api = {
  trades: (p) => get('/trades', p),
  trade: (id) => get(`/trades/${id}`),
  stats: () => get('/stats'),
  timeseries: (days = 90) => get('/stats/timeseries', { days }),
  sectorStats: (days = 90) => get('/stats/sectors', { days }),
  members: (p) => get('/members', p),
  member: (id) => get(`/members/${id}`),
  tickers: (p) => get('/tickers', p),
  ticker: (sym) => get(`/tickers/${encodeURIComponent(sym)}`),
  tickerBars: (sym, days = 365) => get(`/tickers/${encodeURIComponent(sym)}/bars`, { days }),
  tickerEvents: (sym) => get(`/tickers/${encodeURIComponent(sym)}/events`),
  filings: () => get('/filings'),
  signals: (p) => get('/signals', p),
  ideas: (p) => get('/ideas', p),
  leaderboard: (p) => get('/leaderboard', p),
  aiSummary: (window = 7) => get('/ai/summary', { window }),
  aiMemberSummary: (id, window = 30) => get(`/ai/summary/member/${id}`, { window }),
  watchlist: () => get('/watchlist'),
  watchlistFeed: () => get('/watchlist/feed'),
  watchAdd: (kind, value) => send('POST', '/watchlist', { kind, value }),
  watchRemove: (id) => send('DELETE', `/watchlist/${id}`),
  strategies: () => get('/strategies'),
  strategy: (key) => get(`/strategies/${key}`),
  portfolio: () => get('/portfolio/holdings'),
  portfolioOverlap: () => get('/portfolio/overlap'),
  holdingAdd: (h) => send('POST', '/portfolio/holdings', h),
  holdingRemove: (id) => send('DELETE', `/portfolio/holdings/${id}`),
  status: () => get('/status'),
  tickerNews: (sym) => get(`/tickers/${encodeURIComponent(sym)}/news`),
  committees: () => get('/committees'),
  committee: (name) => get(`/committees/${encodeURIComponent(name)}`),
  legislativeEvents: (p) => get('/legislative-events', p),
  reconciliation: () => get('/reconciliation'),
  disclosureLag: (days = 365) => get('/analytics/disclosure-lag', { days }),
}

export function exportCsv() {
  window.open('/api/export/trades.csv', '_blank')
}

export function pct(n, digits = 1) {
  if (n == null) return '—'
  const v = n * 100
  return (v >= 0 ? '+' : '') + v.toFixed(digits) + '%'
}

const SIGNAL_LABELS = {
  cluster_buy: 'cluster buy',
  cluster_sell: 'cluster dump',
  large: 'large',
  options: 'options',
  late_disclosure: 'late',
  anomaly: 'anomaly',
  conflict: 'conflict',
  corp_event: '8-K',
  legislative_context: 'policy context',
}
export function signalLabel(t) {
  return SIGNAL_LABELS[t] || t
}

// 0-100 conviction -> color band
export function convictionClass(score) {
  if (score == null) return ''
  if (score >= 60) return 'conv-high'
  if (score >= 35) return 'conv-mid'
  return 'conv-low'
}

export function money(n) {
  if (n == null) return '—'
  return '$' + Number(n).toLocaleString()
}

export function amountRange(t) {
  if (t.amount_range) return t.amount_range
  if (t.amount_min == null) return '—'
  if (t.amount_max == null) return money(t.amount_min) + '+'
  return `${money(t.amount_min)} – ${money(t.amount_max)}`
}

export function typeClass(t) {
  return { purchase: 'buy', sale: 'sell', exchange: 'exch' }[t] || 'other'
}

// Party -> single-letter tag (handles "Democrat"/"Republican"/"Independent" and "D"/"R"/"I").
export function partyLetter(party) {
  if (!party) return null
  const c = party.trim()[0].toUpperCase()
  return ['D', 'R', 'I', 'L', 'G'].includes(c) ? c : null
}

export function compactMoney(n) {
  if (n == null || n === 0) return '—'
  const a = Math.abs(n)
  if (a >= 1e9) return '$' + (n / 1e9).toFixed(1) + 'B'
  if (a >= 1e6) return '$' + (n / 1e6).toFixed(1) + 'M'
  if (a >= 1e3) return '$' + Math.round(n / 1e3) + 'K'
  return '$' + Math.round(n)
}
