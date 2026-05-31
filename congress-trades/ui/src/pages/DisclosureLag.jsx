import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../api.js'
import PartyBadge from '../components/PartyBadge.jsx'
import TradeTable from '../components/TradeTable.jsx'

function pct(n) {
  if (n == null) return '—'
  return `${Math.round(n * 100)}%`
}

export default function DisclosureLag() {
  const [days, setDays] = useState(365)
  const [data, setData] = useState(undefined)

  useEffect(() => {
    setData(undefined)
    api.disclosureLag(days).then(setData).catch(() => setData(null))
  }, [days])

  return (
    <>
      <h1>Disclosure Lag</h1>
      <p className="note">Measures the delay between transaction date and public disclosure date. This is the app’s honesty layer: congressional trades are public only after filing, often days or weeks later.</p>
      <div className="filters">
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
          <option value={90}>90 days</option>
          <option value={365}>1 year</option>
          <option value={1095}>3 years</option>
        </select>
      </div>

      {data === undefined ? <div className="loading">Loading…</div>
        : data === null ? <p className="muted">Couldn’t load lag analytics.</p>
        : (
          <>
            <div className="cards">
              <div className="card"><div className="label">Trades measured</div><div className="big num">{data.total?.toLocaleString()}</div></div>
              <div className="card"><div className="label">Avg lag</div><div className="big num">{Math.round(data.avg_lag_days || 0)}d</div></div>
              <div className="card"><div className="label">45d+ late rate</div><div className="big num">{pct(data.late_rate)}</div></div>
              <div className="card"><div className="label">Window</div><div className="big num">{data.window_days}d</div></div>
            </div>

            <div className="grid-2">
              <div className="panel">
                <h3>Lag distribution</h3>
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={data.histogram || []}>
                    <CartesianGrid stroke="rgba(139,148,158,.16)" vertical={false} />
                    <XAxis dataKey="bucket" tick={{ fill: 'var(--muted)', fontSize: 11 }} />
                    <YAxis tick={{ fill: 'var(--muted)', fontSize: 11 }} />
                    <Tooltip contentStyle={{ background: 'var(--panel)', border: '1px solid var(--border)' }} />
                    <Bar dataKey="count" fill="var(--accent)" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <div className="panel">
                <h3>By chamber</h3>
                {(data.by_chamber || []).map((r) => (
                  <div key={r.chamber} className="metric-row">
                    <span>{r.chamber}</span>
                    <span className="num">{Math.round(r.avg_lag_days)}d avg</span>
                    <span className="muted">{pct(r.late_rate)} late</span>
                  </div>
                ))}
                <h3 style={{ marginTop: 18 }}>By party</h3>
                {(data.by_party || []).map((r) => (
                  <div key={r.party} className="metric-row">
                    <span>{r.party}</span>
                    <span className="num">{Math.round(r.avg_lag_days)}d avg</span>
                    <span className="muted">{pct(r.late_rate)} late</span>
                  </div>
                ))}
              </div>
            </div>

            <h2>Slowest disclosure averages</h2>
            <div className="panel" style={{ padding: 0, overflowX: 'auto' }}>
              <table>
                <thead><tr><th>Member</th><th className="right">Avg lag</th><th className="right">45d+ rate</th><th className="right">Trades</th></tr></thead>
                <tbody>
                  {(data.worst_members || []).map((m) => (
                    <tr key={m.member_id}>
                      <td><Link to={`/members/${m.member_id}`}>{m.member}</Link> <PartyBadge party={m.party} /> <span className="muted">{m.state || ''}</span></td>
                      <td className="right num">{Math.round(m.avg_lag_days)}d</td>
                      <td className="right num">{pct(m.late_rate)}</td>
                      <td className="right num">{m.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <h2>Latest long-lag disclosures</h2>
            <TradeTable items={data.late_trades || []} />
          </>
        )}
    </>
  )
}
