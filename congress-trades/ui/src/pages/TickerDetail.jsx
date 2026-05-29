import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api.js'
import TradeTable from '../components/TradeTable.jsx'

export default function TickerDetail() {
  const { symbol } = useParams()
  const [data, setData] = useState(null)

  useEffect(() => { setData(null); api.ticker(symbol).then(setData).catch(() => setData(null)) }, [symbol])

  if (!data) return <div className="loading">Loading…</div>

  return (
    <>
      <h1>{data.ticker}</h1>
      <p className="muted">{data.count} trades</p>
      <div className="cards">
        {Object.entries(data.by_transaction_type || {}).map(([k, v]) => (
          <div className="card" key={k}><div className="label">{k}</div><div className="big">{v}</div></div>
        ))}
      </div>
      <h2>Trades</h2>
      <TradeTable items={data.items} />
    </>
  )
}
