import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, compactMoney } from '../api.js'

export default function Committees() {
  const [list, setList] = useState(undefined)
  const [selected, setSelected] = useState(null)
  const [detail, setDetail] = useState(null)

  useEffect(() => { api.committees().then(setList).catch(() => setList(null)) }, [])
  useEffect(() => {
    if (!selected) return
    setDetail(undefined)
    api.committee(selected).then(setDetail).catch(() => setDetail(null))
  }, [selected])

  if (list === undefined) return <div className="loading">Loading…</div>
  if (list === null) return <p className="muted">Couldn’t load committees.</p>

  return (
    <>
      <h1>Committee Exposure</h1>
      <p className="note">Committee context is a risk/explainability layer, not an accusation. It highlights where disclosed trades overlap with committee jurisdictions and sectors.</p>
      <div className="grid-2">
        <div className="panel" style={{ padding: 0, overflowX: 'auto' }}>
          <table>
            <thead><tr><th>Committee</th><th className="right">Members</th><th className="right">Trades</th><th className="right">Volume</th></tr></thead>
            <tbody>
              {(list.items || []).map((c) => (
                <tr key={c.committee} onClick={() => setSelected(c.committee)} className={selected === c.committee ? 'row-active' : ''}>
                  <td>
                    <button className="linklike">{c.committee}</button>
                    <div>{(c.sectors || []).slice(0, 3).map((s) => <span key={s} className="chip">{s}</span>)}</div>
                  </td>
                  <td className="right num">{c.members}</td>
                  <td className="right num">{c.trades}</td>
                  <td className="right num">{compactMoney(c.volume)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="panel">
          {!selected ? <p className="muted">Pick a committee to inspect members and traded tickers.</p>
            : detail === undefined ? <div className="loading">Loading…</div>
            : detail === null ? <p className="muted">Couldn’t load committee.</p>
            : (
              <>
                <h3>{detail.committee}</h3>
                <h2>Members</h2>
                {(detail.members || []).slice(0, 20).map((m) => (
                  <div key={m.id} className="compact-row">
                    <Link to={`/members/${m.id}`}>{m.full_name}</Link>
                    <span className="muted">{m.party?.[0] || ''}{m.state ? `-${m.state}` : ''}{m.district || ''}</span>
                  </div>
                ))}
                <h2>Top Traded Tickers</h2>
                {(detail.tickers || []).slice(0, 15).map((t) => (
                  <div key={t.ticker} className="compact-row">
                    <Link to={`/tickers/${t.ticker}`}>{t.ticker}</Link>
                    <span className="muted">{t.sector || t.company || ''}</span>
                    <span className="num">{compactMoney(t.volume)}</span>
                  </div>
                ))}
              </>
            )}
        </div>
      </div>
    </>
  )
}
