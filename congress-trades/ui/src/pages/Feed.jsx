import { useEffect, useState } from 'react'
import { api } from '../api.js'
import TradeTable from '../components/TradeTable.jsx'

const EMPTY = { chamber: '', transaction_type: '', source: '', ticker: '', q: '' }

export default function Feed() {
  const [filters, setFilters] = useState(EMPTY)
  const [data, setData] = useState(null)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const limit = 100

  useEffect(() => {
    setLoading(true)
    api.trades({ ...filters, limit, offset })
      .then(setData)
      .catch(() => setData({ items: [], total: 0 }))
      .finally(() => setLoading(false))
  }, [filters, offset])

  const set = (k) => (e) => { setOffset(0); setFilters((f) => ({ ...f, [k]: e.target.value })) }

  return (
    <>
      <h1>Trade Feed</h1>
      <div className="filters">
        <input placeholder="Search asset / member / ticker" value={filters.q} onChange={set('q')} />
        <input placeholder="Ticker" value={filters.ticker} onChange={set('ticker')} style={{ minWidth: 90 }} />
        <select value={filters.chamber} onChange={set('chamber')}>
          <option value="">All chambers</option>
          <option value="house">House</option>
          <option value="senate">Senate</option>
        </select>
        <select value={filters.transaction_type} onChange={set('transaction_type')}>
          <option value="">All types</option>
          <option value="purchase">Purchase</option>
          <option value="sale">Sale</option>
          <option value="exchange">Exchange</option>
        </select>
        <select value={filters.source} onChange={set('source')}>
          <option value="">All sources</option>
          <option value="house_primary">House (self-parsed)</option>
          <option value="senate_primary">Senate (self-parsed)</option>
          <option value="lambda">Lambda (live)</option>
        </select>
      </div>

      {loading ? <div className="loading">Loading…</div> : (
        <>
          <p className="muted">{data.total?.toLocaleString()} matching trades</p>
          <TradeTable items={data.items} />
          <div className="pager">
            <button className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>← Prev</button>
            <span className="muted">{offset + 1}–{offset + (data.items?.length || 0)}</span>
            <button className="btn" disabled={offset + limit >= data.total} onClick={() => setOffset(offset + limit)}>Next →</button>
          </div>
        </>
      )}
    </>
  )
}
