import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, compactMoney } from '../api.js'
import { Empty } from '../components/Skeleton.jsx'

export default function Portfolio() {
  const [holdings, setHoldings] = useState([])
  const [overlap, setOverlap] = useState({})
  const [readonly, setReadonly] = useState(false)
  const [ticker, setTicker] = useState('')

  const load = () => {
    api.portfolio().then((d) => { setHoldings(d.items || []); setReadonly(d.readonly) }).catch(() => {})
    api.portfolioOverlap().then((d) => {
      const m = {}; for (const o of d.items || []) m[o.ticker] = o; setOverlap(m)
    }).catch(() => {})
  }
  useEffect(load, [])

  const add = async (e) => {
    e.preventDefault()
    if (!ticker.trim()) return
    await api.holdingAdd({ ticker: ticker.trim().toUpperCase() }).catch(() => {})
    setTicker(''); load()
  }
  const remove = async (id) => { await api.holdingRemove(id).catch(() => {}); load() }

  return (
    <>
      <h1>My Portfolio</h1>
      <p className="note">A personal paper-portfolio. See where your holdings overlap with recent congressional activity. Not linked to any broker; not advice.</p>
      {!readonly && (
        <form className="filters" onSubmit={add}>
          <input placeholder="Add ticker (e.g. NVDA)" value={ticker} onChange={(e) => setTicker(e.target.value)} />
          <button className="btn" type="submit">Add</button>
        </form>
      )}
      {holdings.length === 0 ? (
        <Empty glyph="📁">{readonly ? 'No holdings.' : 'Add a ticker to track congressional overlap.'}</Empty>
      ) : (
        <div className="panel" style={{ padding: 0, overflowX: 'auto' }}>
          <table>
            <thead><tr><th>Ticker</th><th>Congress (90d)</th><th className="right">Buyers</th><th className="right">Sellers</th><th>Last disclosed</th>{!readonly && <th></th>}</tr></thead>
            <tbody>
              {holdings.map((h) => {
                const o = overlap[h.ticker] || {}
                const net = o.net_notional || 0
                return (
                  <tr key={h.id}>
                    <td><Link to={`/tickers/${h.ticker}`}>{h.ticker}</Link></td>
                    <td>
                      {net > 0 ? <span className="tag buy">net buying ▲ {compactMoney(net)}</span>
                        : net < 0 ? <span className="tag sell">net selling ▼ {compactMoney(-net)}</span>
                        : <span className="muted">no activity</span>}
                    </td>
                    <td className="right num pos">{o.buyers || ''}</td>
                    <td className="right num neg">{o.sellers || ''}</td>
                    <td className="muted nowrap">{o.last_seen || '—'}</td>
                    {!readonly && <td><button className="star on" onClick={() => remove(h.id)} title="Remove">✕</button></td>}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
