import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api.js'
import TradeTable from '../components/TradeTable.jsx'

export default function MemberDetail() {
  const { id } = useParams()
  const [data, setData] = useState(null)

  useEffect(() => { setData(null); api.member(id).then(setData).catch(() => setData(null)) }, [id])

  if (!data) return <div className="loading">Loading…</div>
  const m = data.member

  return (
    <>
      <h1>{m.full_name}</h1>
      <p className="muted">
        <span className={`chamber-${m.chamber}`}>{m.chamber}</span>
        {m.party ? ` · ${m.party}` : ''}{m.state ? ` · ${m.state}${m.district ? `-${m.district}` : ''}` : ''}
        {` · ${m.trade_count} trades`}
      </p>

      <div className="cards">
        {Object.entries(data.by_transaction_type || {}).map(([k, v]) => (
          <div className="card" key={k}><div className="label">{k}</div><div className="big">{v}</div></div>
        ))}
      </div>

      {data.top_tickers?.length > 0 && (
        <>
          <h2>Top tickers</h2>
          <div className="panel">
            {data.top_tickers.map((t) => (
              <span key={t.ticker} className="tag src" style={{ marginRight: 8 }}>{t.ticker} · {t.count}</span>
            ))}
          </div>
        </>
      )}

      <h2>Trades</h2>
      <TradeTable items={data.trades} showMember={false} />
    </>
  )
}
