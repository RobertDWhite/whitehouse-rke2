import { useEffect, useState } from 'react'
import { api, signalLabel } from '../api.js'
import TradeTable from '../components/TradeTable.jsx'

const TYPES = ['cluster_buy', 'large', 'options', 'late_disclosure', 'anomaly']

export default function Signals() {
  const [type, setType] = useState('')
  const [data, setData] = useState(undefined)

  useEffect(() => {
    setData(undefined)
    api.signals({ signal_type: type, limit: 200 }).then(setData).catch(() => setData(null))
  }, [type])

  return (
    <>
      <h1>Signals</h1>
      <p className="note">
        Computed "interesting" patterns in disclosed trades — cluster buys (multiple members, same
        ticker), large notional, options, late disclosures, and per-member anomalies. Descriptive
        only; not advice or an allegation of wrongdoing.
      </p>
      <div className="filters">
        <button className={`btn ${type === '' ? 'active' : ''}`} onClick={() => setType('')}>All</button>
        {TYPES.map((t) => (
          <button key={t} className={`btn ${type === t ? 'active' : ''}`} onClick={() => setType(t)}>
            {signalLabel(t)}{data?.by_type?.[t] != null ? ` (${data.by_type[t]})` : ''}
          </button>
        ))}
      </div>
      {data === undefined ? <div className="loading">Loading…</div>
        : data === null ? <p className="muted">Couldn’t load signals.</p>
        : <TradeTable items={data.items} />}
    </>
  )
}
