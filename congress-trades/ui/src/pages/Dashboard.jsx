import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, compactMoney } from '../api.js'
import AiInsights from '../components/AiInsights.jsx'
import BuySellTimeline from '../components/BuySellTimeline.jsx'
import PartyBadge from '../components/PartyBadge.jsx'
import TradeTable from '../components/TradeTable.jsx'
import { SkeletonCards, SkeletonTable } from '../components/Skeleton.jsx'

function delta(cur, prev) {
  if (!prev) return null
  const d = (cur - prev) / prev
  return { txt: (d >= 0 ? '+' : '') + Math.round(d * 100) + '%', cls: d >= 0 ? 'pos' : 'neg' }
}

function Kpi({ label, value, d }) {
  return (
    <div className="card">
      <div className="label">{label}</div>
      <div className="big num">{value}</div>
      {d && <div className={`delta ${d.cls}`}>{d.txt} vs prior</div>}
    </div>
  )
}

export default function Dashboard() {
  const [stats, setStats] = useState(null)
  const [ts, setTs] = useState(null)

  useEffect(() => {
    api.stats().then(setStats).catch(() => setStats(false))
    api.timeseries(120).then((d) => setTs(d.items)).catch(() => setTs([]))
  }, [])

  return (
    <>
      <h1>Dashboard</h1>
      <AiInsights defaultWindow={7} />

      {!stats ? <SkeletonCards n={6} /> : (
        <div className="cards">
          <Kpi label="Total trades" value={stats.total_trades?.toLocaleString()} />
          <Kpi label="Trades · 7d" value={(stats.kpi?.count_7d || 0).toLocaleString()} d={delta(stats.kpi?.count_7d, stats.kpi?.count_prior_7d)} />
          <Kpi label="Volume · 30d" value={compactMoney(stats.kpi?.volume_30d)} d={delta(stats.kpi?.volume_30d, stats.kpi?.volume_prior_30d)} />
          <Kpi label="House" value={(stats.by_chamber?.house || 0).toLocaleString()} />
          <Kpi label="Senate" value={(stats.by_chamber?.senate || 0).toLocaleString()} />
          <Kpi label="Self-parsed" value={((stats.by_source?.house_primary || 0) + (stats.by_source?.senate_primary || 0)).toLocaleString()} />
        </div>
      )}

      <div className="grid-2">
        <div className="panel">
          <h3>Buy / sell activity (weekly, disclosed)</h3>
          {ts === null ? <div className="loading">Loading…</div> : <BuySellTimeline data={ts} />}
        </div>
        <div className="panel">
          <h3>Hot tickers · 7d</h3>
          {!stats ? <div className="loading">Loading…</div> : (
            <table>
              <tbody>
                {(stats.hot_tickers_7d || []).map((h) => {
                  const tot = h.buys + h.sells || 1
                  return (
                    <tr key={h.ticker}>
                      <td><Link to={`/tickers/${h.ticker}`}>{h.ticker}</Link></td>
                      <td style={{ width: '60%' }}>
                        <div className="conv-bar" style={{ width: '100%', height: 8 }}>
                          <span style={{ width: `${(h.buys / tot) * 100}%`, background: 'var(--buy)', float: 'left', height: '100%' }} />
                          <span style={{ width: `${(h.sells / tot) * 100}%`, background: 'var(--sell)', float: 'left', height: '100%' }} />
                        </div>
                      </td>
                      <td className="right num muted">{h.buys}/{h.sells}</td>
                    </tr>
                  )
                })}
                {stats.hot_tickers_7d?.length === 0 && <tr><td className="muted">Quiet week.</td></tr>}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="grid-2">
        <div className="panel">
          <h3>Most active traders</h3>
          {!stats ? <div className="loading">Loading…</div> : (
            <table>
              <tbody>
                {(stats.top_traders || []).slice(0, 8).map((m, i) => (
                  <tr key={m.member_id}>
                    <td><span className={`rank rank-${i + 1 <= 3 ? i + 1 : ''}`}>{i + 1}</span></td>
                    <td className="nowrap"><Link to={`/members/${m.member_id}`}>{m.member}</Link> <PartyBadge party={m.party} /></td>
                    <td className="muted">{m.state ? `${m.state}${m.district ? `-${m.district}` : ''}` : ''}</td>
                    <td className="right num">{m.count.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="panel">
          <h3>Most-traded tickers</h3>
          {!stats ? <div className="loading">Loading…</div> : (
            <table>
              <tbody>
                {(stats.top_tickers || []).slice(0, 8).map((t) => (
                  <tr key={t.ticker}><td><Link to={`/tickers/${t.ticker}`}>{t.ticker}</Link></td><td className="right num">{t.count.toLocaleString()}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <h2>Recently disclosed</h2>
      {!stats ? <SkeletonTable rows={8} /> : <TradeTable items={stats.recent} />}
    </>
  )
}
