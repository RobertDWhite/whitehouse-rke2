import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'

export default function Members() {
  const [filters, setFilters] = useState({ chamber: '', q: '' })
  const [items, setItems] = useState(null)

  useEffect(() => {
    setItems(null)
    api.members(filters).then((d) => setItems(d.items)).catch(() => setItems([]))
  }, [filters])

  const set = (k) => (e) => setFilters((f) => ({ ...f, [k]: e.target.value }))

  return (
    <>
      <h1>Members</h1>
      <div className="filters">
        <input placeholder="Search name" value={filters.q} onChange={set('q')} />
        <select value={filters.chamber} onChange={set('chamber')}>
          <option value="">All chambers</option>
          <option value="house">House</option>
          <option value="senate">Senate</option>
        </select>
      </div>
      {!items ? <div className="loading">Loading…</div> : (
        <div className="panel" style={{ padding: 0 }}>
          <table>
            <thead><tr><th>Name</th><th>Chamber</th><th>Party</th><th>State</th><th className="right">Trades</th></tr></thead>
            <tbody>
              {items.map((m) => (
                <tr key={m.id}>
                  <td><Link to={`/members/${m.id}`}>{m.full_name}</Link></td>
                  <td className={`chamber-${m.chamber}`}>{m.chamber || '—'}</td>
                  <td className="muted">{m.party || '—'}</td>
                  <td className="muted">{m.state || '—'}{m.district ? `-${m.district}` : ''}</td>
                  <td className="right">{(m.trade_count || 0).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
