import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, compactMoney } from '../api.js'
import PartyBadge from '../components/PartyBadge.jsx'

export default function Members() {
  const [filters, setFilters] = useState({ chamber: '', party: '', q: '' })
  const [items, setItems] = useState(null)
  const [sort, setSort] = useState('volume') // 'volume' | 'trades' | 'name'

  useEffect(() => {
    setItems(null)
    api.members(filters).then((d) => setItems(d.items)).catch(() => setItems([]))
  }, [filters])

  const set = (k) => (e) => setFilters((f) => ({ ...f, [k]: e.target.value }))

  const sorted = useMemo(() => {
    if (!items) return null
    const s = [...items]
    if (sort === 'volume') s.sort((a, b) => (b.est_volume || 0) - (a.est_volume || 0))
    else if (sort === 'trades') s.sort((a, b) => (b.trade_count || 0) - (a.trade_count || 0))
    else s.sort((a, b) => a.full_name.localeCompare(b.full_name))
    return s
  }, [items, sort])

  const maxVol = useMemo(
    () => (sorted ? Math.max(1, ...sorted.map((m) => m.est_volume || 0)) : 1),
    [sorted],
  )

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
        <select value={filters.party} onChange={set('party')}>
          <option value="">All parties</option>
          <option value="Republican">Republican</option>
          <option value="Democrat">Democrat</option>
          <option value="Independent">Independent</option>
        </select>
        <select value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="volume">Sort: Disclosed volume</option>
          <option value="trades">Sort: Trade count</option>
          <option value="name">Sort: Name</option>
        </select>
      </div>

      <p className="note">
        "Disclosed volume" = sum of the midpoints of each trade's disclosed amount range. It's a
        proxy for trading activity, not net worth (true net worth comes from the separate annual
        financial-disclosure filings).
      </p>

      {!sorted ? (
        <div className="loading">Loading…</div>
      ) : (
        <div className="panel" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Chamber</th>
                <th>State</th>
                <th className="right">Trades</th>
                <th>Disclosed volume</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((m) => (
                <tr key={m.id}>
                  <td className="nowrap">
                    <Link to={`/members/${m.id}`}>{m.full_name}</Link> <PartyBadge party={m.party} />
                  </td>
                  <td className={`chamber-${m.chamber}`}>{m.chamber || '—'}</td>
                  <td className="muted">{m.state || '—'}{m.district ? `-${m.district}` : ''}</td>
                  <td className="right">{(m.trade_count || 0).toLocaleString()}</td>
                  <td>
                    <div className="volbar-wrap">
                      <div
                        className="volbar"
                        style={{ width: `${Math.max(2, ((m.est_volume || 0) / maxVol) * 100)}%` }}
                      />
                      <span className="volbar-label">{compactMoney(m.est_volume)}</span>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
