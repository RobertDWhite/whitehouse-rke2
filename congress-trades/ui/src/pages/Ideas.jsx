import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, compactMoney } from '../api.js'
import Conviction from '../components/Conviction.jsx'
import TradeTable from '../components/TradeTable.jsx'
import { SkeletonTable } from '../components/Skeleton.jsx'

function AiWatchlist() {
  const [d, setD] = useState(undefined)
  useEffect(() => { api.aiSummary(30).then(setD).catch(() => setD(null)) }, [])
  if (!d || !d.watchlist || d.watchlist.length === 0) return null
  return (
    <div className="panel" style={{ marginBottom: 24, borderLeft: '3px solid var(--accent)' }}>
      <h3>🤖 AI watchlist — disclosed accumulation (research only)</h3>
      <div className="grid-3">
        {d.watchlist.map((w) => (
          <div className="idea-card" key={w.ticker}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Link to={`/tickers/${w.ticker}`} style={{ fontWeight: 700, fontSize: 16 }}>{w.ticker}</Link>
              <Conviction score={w.conviction} />
            </div>
            <div className="reason">{w.reason}</div>
            <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
              {w.buyers} buyer{w.buyers === 1 ? '' : 's'} · net {compactMoney(w.net_notional)}
            </div>
          </div>
        ))}
      </div>
      <p className="note">{d.disclaimer || ''}</p>
    </div>
  )
}

export default function Ideas() {
  const [d, setD] = useState(undefined)
  useEffect(() => { api.ideas({ window: 90 }).then(setD).catch(() => setD(null)) }, [])

  return (
    <>
      <h1>Ideas</h1>
      <div className="disclaimer-banner">
        Informational only — not financial advice. Everything here is <strong>publicly disclosed</strong> under the
        STOCK Act and reported with a delay of up to <strong>45 days</strong>: it is not real-time and not a signal to
        trade now. Disclosed trades are legal. Returns shown are a hypothetical follower's, from the public disclosure
        date, vs SPY. Past performance does not predict future results.
      </div>

      <AiWatchlist />

      <h2>Congress is accumulating <span className="muted" style={{ fontWeight: 400, fontSize: 13 }}>(net buys − sells, 90d)</span></h2>
      {d === undefined ? <SkeletonTable rows={8} />
        : d === null ? <p className="muted">Couldn’t load ideas.</p>
        : (
        <div className="panel" style={{ padding: 0, overflowX: 'auto' }}>
          <table>
            <thead><tr><th>Ticker</th><th>Sector</th><th className="right">Net accumulated</th><th className="right">Buyers</th><th>Conviction</th><th>Last disclosed</th></tr></thead>
            <tbody>
              {(d.accumulation || []).map((a) => (
                <tr key={a.ticker}>
                  <td><Link to={`/tickers/${a.ticker}`}>{a.ticker}</Link>{a.company ? <div className="muted" style={{ fontSize: 11 }}>{a.company}</div> : null}</td>
                  <td className="muted">{a.sector || '—'}</td>
                  <td className="right num">{compactMoney(a.net_notional)}</td>
                  <td className="right num">{a.buyers}</td>
                  <td><Conviction score={a.conviction} /></td>
                  <td className="muted nowrap">{a.last_seen || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {d && d.high_conviction?.length > 0 && (<><h2>Highest-conviction recent buys</h2><TradeTable items={d.high_conviction} /></>)}
      {d && d.cluster_buys?.length > 0 && (<><h2>Cluster buying (multiple members, same ticker)</h2><TradeTable items={d.cluster_buys} /></>)}
      {d && d.cluster_dumps?.length > 0 && (<><h2>⚠️ Cluster dumping (multiple members selling)</h2><TradeTable items={d.cluster_dumps} /></>)}
      {d && d.conflicts?.length > 0 && (<><h2>Committee-relevant trades (potential conflict)</h2><TradeTable items={d.conflicts} /></>)}
      {d && d.unusual_options?.length > 0 && (<><h2>Unusual options activity</h2><TradeTable items={d.unusual_options} /></>)}
    </>
  )
}
