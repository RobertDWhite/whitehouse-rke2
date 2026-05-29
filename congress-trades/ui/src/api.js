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

// Party -> single-letter tag (handles "Democrat"/"Republican"/"Independent" and "D"/"R"/"I").
export function partyLetter(party) {
  if (!party) return null
  const c = party.trim()[0].toUpperCase()
  return ['D', 'R', 'I', 'L'].includes(c) ? c : null
}

export function compactMoney(n) {
  if (n == null || n === 0) return '—'
  const a = Math.abs(n)
  if (a >= 1e9) return '$' + (n / 1e9).toFixed(1) + 'B'
  if (a >= 1e6) return '$' + (n / 1e6).toFixed(1) + 'M'
  if (a >= 1e3) return '$' + Math.round(n / 1e3) + 'K'
  return '$' + Math.round(n)
}
