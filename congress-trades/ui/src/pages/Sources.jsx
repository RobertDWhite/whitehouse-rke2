import { useEffect, useState } from 'react'
import { api } from '../api.js'

const SOURCE_LABEL = {
  house_primary: 'House — self-parsed (authoritative)',
  senate_primary: 'Senate — self-parsed (authoritative)',
  lambda: 'Lambda Finance — third-party live feed',
}

export default function Sources() {
  const [data, setData] = useState(null)

  useEffect(() => { api.filings().then(setData).catch(() => setData(null)) }, [])

  if (!data) return <div className="loading">Loading…</div>

  return (
    <>
      <h1>Data sources & provenance</h1>
      <p className="note">
        Primary-source parsers (House Clerk bulk PDFs, Senate eFD HTML) are authoritative and
        supersede the third-party feed. Non-zero <em>self-parsed</em> counts below prove the app
        stands on its own without relying on any third party.
      </p>

      <h2>Trades by source</h2>
      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Source</th><th className="right">Trades</th><th>Last ingest</th></tr></thead>
          <tbody>
            {Object.entries(data.trades_by_source || {}).map(([src, n]) => (
              <tr key={src}>
                <td>{SOURCE_LABEL[src] || src}</td>
                <td className="right">{n.toLocaleString()}</td>
                <td className="muted">{data.last_fetch_by_source?.[src.replace('_primary', '')] || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>Filings parsed (primary sources)</h2>
      <div className="cards">
        {Object.entries(data.filings_by_source || {}).map(([src, n]) => (
          <div className="card" key={src}><div className="label">{src}</div><div className="big">{n.toLocaleString()}</div></div>
        ))}
      </div>

      <h2>Parse status</h2>
      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Status</th><th className="right">Filings</th></tr></thead>
          <tbody>
            {Object.entries(data.filings_by_parse_status || {}).map(([s, n]) => (
              <tr key={s}><td>{s}</td><td className="right">{n.toLocaleString()}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="note">
        Status legend — <strong>parsed</strong>: machine-readable text · <strong>ocr</strong>:
        scanned, recovered via OCR · <strong>paper</strong>: scanned, OCR pending/failed ·
        <strong> error</strong>: fetch/parse failure.
      </p>
    </>
  )
}
