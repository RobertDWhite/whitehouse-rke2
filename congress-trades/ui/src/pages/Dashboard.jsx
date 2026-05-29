import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../api.js'
import AiInsights from '../components/AiInsights.jsx'
import PartyBadge from '../components/PartyBadge.jsx'
import TradeTable from '../components/TradeTable.jsx'

export default function Dashboard() {
  const [stats, setStats] = useState(null)

  useEffect(() => { api.stats().then(setStats).catch(() => setStats(null)) }, [])

  if (!stats) return <div className="loading">Loading…</div>

  const typeData = Object.entries(stats.by_transaction_type || {}).map(([name, value]) => ({ name, value }))
  const tickerData = (stats.top_tickers || []).slice(0, 12).map((t) => ({ name: t.ticker, value: t.count }))
  const colors = { purchase: '#3fb950', sale: '#f85149', exchange: '#d29922', other: '#8b949e' }

  return (
    <>
      <h1>Dashboard</h1>
      <AiInsights defaultWindow={7} />
      <div className="cards">
        <div className="card"><div className="label">Total Trades</div><div className="big">{stats.total_trades?.toLocaleString()}</div></div>
        <div className="card"><div className="label">House</div><div className="big chamber-house">{(stats.by_chamber?.house || 0).toLocaleString()}</div></div>
        <div className="card"><div className="label">Senate</div><div className="big chamber-senate">{(stats.by_chamber?.senate || 0).toLocaleString()}</div></div>
        <div className="card"><div className="label">Self-parsed</div><div className="big">{((stats.by_source?.house_primary || 0) + (stats.by_source?.senate_primary || 0)).toLocaleString()}</div></div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginTop: 20 }}>
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>By transaction type</h2>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={typeData}>
              <XAxis dataKey="name" stroke="#8b949e" />
              <YAxis stroke="#8b949e" />
              <Tooltip contentStyle={{ background: '#161b22', border: '1px solid #30363d' }} />
              <Bar dataKey="value">
                {typeData.map((d) => <Cell key={d.name} fill={colors[d.name] || '#58a6ff'} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Most-traded tickers</h2>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={tickerData} layout="vertical" margin={{ left: 20 }}>
              <XAxis type="number" stroke="#8b949e" />
              <YAxis type="category" dataKey="name" width={60} stroke="#8b949e" />
              <Tooltip contentStyle={{ background: '#161b22', border: '1px solid #30363d' }} />
              <Bar dataKey="value" fill="#58a6ff" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <h2>Most active traders</h2>
      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Member</th><th>Party</th><th>Chamber</th><th>District</th><th className="right">Trades</th></tr></thead>
          <tbody>
            {(stats.top_traders || []).map((m) => (
              <tr key={m.member_id}>
                <td className="nowrap"><Link to={`/members/${m.member_id}`}>{m.member}</Link> <PartyBadge party={m.party} /></td>
                <td className="muted">{m.party || '—'}</td>
                <td className={`chamber-${m.chamber}`}>{m.chamber || '—'}</td>
                <td className="muted">{m.state ? `${m.state}${m.district ? `-${m.district}` : ''}` : '—'}</td>
                <td className="right">{m.count.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>Recently disclosed</h2>
      <TradeTable items={stats.recent} />
    </>
  )
}
