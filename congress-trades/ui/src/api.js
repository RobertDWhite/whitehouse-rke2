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

export const api = {
  trades: (p) => get('/trades', p),
  stats: () => get('/stats'),
  members: (p) => get('/members', p),
  member: (id) => get(`/members/${id}`),
  tickers: (p) => get('/tickers', p),
  ticker: (sym) => get(`/tickers/${encodeURIComponent(sym)}`),
  filings: () => get('/filings'),
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
