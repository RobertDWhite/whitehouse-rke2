import { useEffect, useMemo, useState } from 'react'
import { api } from '../api.js'

function age(seconds) {
  if (seconds == null) return 'never'
  if (seconds < 90) return `${Math.round(seconds)}s`
  if (seconds < 7200) return `${Math.round(seconds / 60)}m`
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h`
  return `${Math.round(seconds / 86400)}d`
}

export default function Status() {
  const [data, setData] = useState(undefined)
  useEffect(() => { api.status().then(setData).catch(() => setData(null)) }, [])

  const sorted = useMemo(() => [...(data?.sources || [])].sort((a, b) => Number(b.stale) - Number(a.stale) || (b.age_seconds || 0) - (a.age_seconds || 0)), [data])

  if (data === undefined) return <div className="loading">Loading…</div>
  if (data === null) return <p className="muted">Couldn’t load status.</p>

  return (
    <>
      <h1>Freshness</h1>
      <div className="cards">
        <div className="card"><div className="label">Trades</div><div className="big num">{data.trades?.toLocaleString()}</div></div>
        <div className="card"><div className="label">Members</div><div className="big num">{data.members?.toLocaleString()}</div></div>
        <div className={`card ${data.stale_sources ? 'status-warn' : ''}`}><div className="label">Stale sources</div><div className="big num">{data.stale_sources}</div></div>
        <div className="card"><div className="label">Latest disclosure</div><div className="big num" style={{ fontSize: 20 }}>{data.latest_disclosure || '—'}</div></div>
      </div>
      <h2>Source health</h2>
      <div className="panel" style={{ padding: 0, overflowX: 'auto' }}>
        <table>
          <thead><tr><th>Source</th><th>Status</th><th>Last success</th><th className="right">Age</th><th className="right">Rows</th><th>Note</th></tr></thead>
          <tbody>
            {sorted.map((s) => (
              <tr key={s.source}>
                <td className="nowrap">{s.source}</td>
                <td><span className={`tag ${s.stale ? 'sell' : 'buy'}`}>{s.stale ? 'stale' : 'fresh'}</span></td>
                <td className="muted nowrap">{s.last_success || '—'}</td>
                <td className="right num">{age(s.age_seconds)}</td>
                <td className="right num">{s.rows ?? '—'}</td>
                <td className="muted">{s.note || ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
