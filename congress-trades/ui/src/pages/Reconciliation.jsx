import { useEffect, useState } from 'react'
import { api } from '../api.js'

const LABELS = {
  missing_primary: 'Comparison feed item not yet found in primary parsers',
  unparsed_filing: 'Primary filing needs OCR/parser attention',
  amount_mismatch: 'Matched row has amount drift',
  missing_ticker: 'Primary row needs ticker mapping',
  mismatch: 'Cross-source mismatch',
}

export default function Reconciliation() {
  const [data, setData] = useState(undefined)
  useEffect(() => { api.reconciliation().then(setData).catch(() => setData(null)) }, [])

  if (data === undefined) return <div className="loading">Loading…</div>
  if (data === null) return <p className="muted">Couldn’t load reconciliation.</p>

  return (
    <>
      <h1>Reconciliation</h1>
      <p className="note">Parser and comparison-feed canaries. These are data-quality issues, not trading signals.</p>
      <div className="cards">
        {Object.entries(data.by_kind || {}).map(([k, v]) => (
          <div className="card" key={k}><div className="label">{k}</div><div className="big num">{v}</div></div>
        ))}
      </div>
      <h2>Open Issues</h2>
      <div className="panel" style={{ padding: 0, overflowX: 'auto' }}>
        <table>
          <thead><tr><th>Kind</th><th className="right">Severity</th><th>Source</th><th>Detail</th><th>Seen</th></tr></thead>
          <tbody>
            {(data.items || []).map((r) => (
              <tr key={r.id}>
                <td><span className={`tag ${r.severity >= 3 ? 'sell' : 'exch'}`}>{LABELS[r.kind] || r.kind}</span></td>
                <td className="right num">{r.severity}</td>
                <td className="muted">{r.comparison_source || '—'}</td>
                <td>
                  {r.detail?.source_url ? <a href={r.detail.source_url} target="_blank" rel="noreferrer">{r.detail.doc_id || r.detail.ticker || 'filing'}</a> : (r.detail?.ticker || r.detail?.doc_id || '—')}
                  <div className="muted">{r.detail?.member || r.detail?.status || r.detail?.amount_range || ''}</div>
                </td>
                <td className="muted nowrap">{r.created_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
