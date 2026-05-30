import { useEffect, useState } from 'react'
import { api, exportCsv } from '../api.js'
import TradeTable from '../components/TradeTable.jsx'

const EMPTY = {
  chamber: '', transaction_type: '', source: '', ticker: '', q: '',
  party: '', state: '', start_date: '', end_date: '', min_amount: '', signal: '',
}

export default function Feed() {
  const [filters, setFilters] = useState(EMPTY)
  const [sort, setSort] = useState('transaction_date')
  const [data, setData] = useState(null)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const limit = 100

  useEffect(() => {
    setLoading(true)
    setError(false)
    api.trades({ ...filters, sort, order: 'desc', limit, offset })
      .then(setData)
      .catch(() => { setData(null); setError(true) })
      .finally(() => setLoading(false))
  }, [filters, sort, offset])

  const set = (k) => (e) => { setOffset(0); setFilters((f) => ({ ...f, [k]: e.target.value })) }

  return (
    <>
      <h1>Trade Feed</h1>
      <div className="filters">
        <input placeholder="Search asset / member / ticker" value={filters.q} onChange={set('q')} />
        <input placeholder="Ticker" value={filters.ticker} onChange={set('ticker')} style={{ minWidth: 80 }} />
        <select value={filters.chamber} onChange={set('chamber')}>
          <option value="">All chambers</option><option value="house">House</option><option value="senate">Senate</option>
        </select>
        <select value={filters.party} onChange={set('party')}>
          <option value="">All parties</option><option value="Republican">R</option><option value="Democrat">D</option><option value="Independent">I</option>
        </select>
        <input placeholder="State" value={filters.state} onChange={set('state')} style={{ minWidth: 60, maxWidth: 70 }} />
        <select value={filters.transaction_type} onChange={set('transaction_type')}>
          <option value="">All types</option><option value="purchase">Purchase</option><option value="sale">Sale</option><option value="exchange">Exchange</option>
        </select>
        <select value={filters.signal} onChange={set('signal')}>
          <option value="">Any signal</option><option value="cluster_buy">Cluster buy</option><option value="large">Large</option>
          <option value="options">Options</option><option value="late_disclosure">Late disclosure</option><option value="anomaly">Anomaly</option>
        </select>
        <select value={filters.min_amount} onChange={set('min_amount')}>
          <option value="">Any amount</option><option value="50000">≥ $50K</option><option value="100000">≥ $100K</option>
          <option value="250000">≥ $250K</option><option value="1000000">≥ $1M</option>
        </select>
        <select value={filters.source} onChange={set('source')}>
          <option value="">All sources</option><option value="house_primary">House (self-parsed)</option>
          <option value="senate_primary">Senate (self-parsed)</option><option value="lambda">Lambda (live)</option>
        </select>
        <input type="date" value={filters.start_date} onChange={set('start_date')} title="Traded on/after" />
        <input type="date" value={filters.end_date} onChange={set('end_date')} title="Traded on/before" />
        <select value={sort} onChange={(e) => { setOffset(0); setSort(e.target.value) }}>
          <option value="transaction_date">Sort: Traded date</option>
          <option value="disclosure_date">Sort: Disclosed date</option>
          <option value="amount">Sort: Amount</option>
        </select>
        <button className="btn" onClick={exportCsv} title="Download CSV">⬇ CSV</button>
      </div>

      {loading ? <div className="loading">Loading…</div>
        : error ? <p className="muted">Couldn’t load trades — try again.</p>
        : (
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
