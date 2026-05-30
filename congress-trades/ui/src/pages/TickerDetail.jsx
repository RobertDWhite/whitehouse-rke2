import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, money, pct } from '../api.js'
import PartyBadge from '../components/PartyBadge.jsx'
import StarToggle from '../components/StarToggle.jsx'
import TradeTable from '../components/TradeTable.jsx'
import { SkeletonCards } from '../components/Skeleton.jsx'

export default function TickerDetail() {
  const { symbol } = useParams()
  const [data, setData] = useState(null)
  const [news, setNews] = useState(null)

  useEffect(() => {
    setData(null); setNews(null)
    api.ticker(symbol).then(setData).catch(() => setData(false))
    api.tickerNews(symbol).then((d) => setNews(d.items || [])).catch(() => setNews([]))
  }, [symbol])

  const byMember = useMemo(() => {
    if (!data?.items) return []
    const map = {}
    for (const t of data.items) {
      if (!t.member_id) continue
      const m = (map[t.member_id] ||= { id: t.member_id, name: t.member, party: t.party, buys: 0, sells: 0 })
      if (t.transaction_type === 'purchase') m.buys++
      else if (t.transaction_type === 'sale') m.sells++
    }
    return Object.values(map).sort((a, b) => b.buys + b.sells - (a.buys + a.sells)).slice(0, 12)
  }, [data])

  const party = useMemo(() => {
    const p = { D: 0, R: 0, I: 0 }
    for (const t of data?.items || []) { const c = t.party?.[0]; if (c && p[c] != null) p[c]++ }
    return p
  }, [data])

  if (data === false) return <p className="muted">Couldn’t load ticker.</p>
  if (!data) return <SkeletonCards n={3} />

  return (
    <>
      <h1>{data.ticker} <StarToggle kind="ticker" value={data.ticker} /></h1>
      <p className="muted">{data.company || ''}{data.sector ? ` · ${data.sector}` : ''} · {data.count} disclosed trades</p>

      <div className="cards">
        {(data.live_price ?? data.price) != null && (
          <div className="card">
            <div className="label">{data.live_price != null ? 'Live price' : 'Latest close'}</div>
            <div className="big num">{money(data.live_price ?? data.price)}{data.live_price != null && <span className="live-dot">●</span>}</div>
            <div className="note">{data.market_state || data.price_as_of || ''}</div>
          </div>
        )}
        {data.sentiment != null && (
          <div className="card">
            <div className="label">Retail sentiment</div>
            <div className={`big num ${data.sentiment >= 0 ? 'pos' : 'neg'}`}>{data.sentiment >= 0 ? 'Bullish' : 'Bearish'} {pct(data.sentiment, 0)}</div>
            <div className="note">StockTwits · {data.sentiment_n} msgs</div>
          </div>
        )}
        {Object.entries(data.by_transaction_type || {}).map(([k, v]) => (
          <div className="card" key={k}><div className="label">{k}</div><div className="big num">{v}</div></div>
        ))}
        <div className="card"><div className="label">Party split</div><div className="big num"><span className="pc-D">{party.D}D</span> / <span className="pc-R">{party.R}R</span></div></div>
      </div>

      {news && news.length > 0 && (
        <div className="panel" style={{ marginTop: 16 }}>
          <h3>Recent news</h3>
          <div className="news-list">
            {news.map((n, i) => (
              <a key={i} href={n.link} target="_blank" rel="noopener noreferrer">{n.title}<span className="src"> · {n.source}</span></a>
            ))}
          </div>
        </div>
      )}

      {byMember.length > 0 && (
        <>
          <h2>Who’s trading {data.ticker}</h2>
          <div className="panel" style={{ padding: 0 }}>
            <table>
              <thead><tr><th>Member</th><th className="right">Buys</th><th className="right">Sells</th></tr></thead>
              <tbody>
                {byMember.map((m) => (
                  <tr key={m.id}>
                    <td className="nowrap"><Link to={`/members/${m.id}`}>{m.name}</Link> <PartyBadge party={m.party} /></td>
                    <td className="right num pos">{m.buys || ''}</td>
                    <td className="right num neg">{m.sells || ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <h2>Trades</h2>
      <TradeTable items={data.items} />
    </>
  )
}
