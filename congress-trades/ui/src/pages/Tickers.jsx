import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api.js'

export default function Tickers() {
  const [items, setItems] = useState(null)
  const [sym, setSym] = useState('')
  const nav = useNavigate()

  useEffect(() => { api.tickers({ limit: 100 }).then((d) => setItems(d.items)).catch(() => setItems([])) }, [])

  const go = (e) => { e.preventDefault(); if (sym.trim()) nav(`/tickers/${sym.trim().toUpperCase()}`) }

  return (
    <>
      <h1>Tickers</h1>
      <form className="filters" onSubmit={go}>
        <input placeholder="Look up a symbol (e.g. NVDA)" value={sym} onChange={(e) => setSym(e.target.value)} />
        <button className="btn" type="submit">Go</button>
      </form>
      <h2>Most-traded</h2>
      {!items ? <div className="loading">Loading…</div> : (
        <div className="panel" style={{ padding: 0 }}>
          <table>
            <thead><tr><th>Ticker</th><th className="right">Trades</th></tr></thead>
            <tbody>
              {items.map((t) => (
                <tr key={t.ticker}>
                  <td><Link to={`/tickers/${t.ticker}`}>{t.ticker}</Link></td>
                  <td className="right">{t.count.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
