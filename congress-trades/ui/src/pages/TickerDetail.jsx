import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { CartesianGrid, Line, LineChart, ReferenceDot, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, money, pct } from '../api.js'
import PartyBadge from '../components/PartyBadge.jsx'
import StarToggle from '../components/StarToggle.jsx'
import TradeTable from '../components/TradeTable.jsx'
import { SkeletonCards } from '../components/Skeleton.jsx'

export default function TickerDetail() {
  const { symbol } = useParams()
  const [data, setData] = useState(null)
  const [news, setNews] = useState(null)
  const [events, setEvents] = useState([])
  const [bars, setBars] = useState([])
  const [govEvents, setGovEvents] = useState([])

  useEffect(() => {
    setData(null); setNews(null)
    api.ticker(symbol).then(setData).catch(() => setData(false))
    api.tickerNews(symbol).then((d) => setNews(d.items || [])).catch(() => setNews([]))
    api.legislativeEvents({ ticker: symbol, limit: 12 }).then((d) => setEvents(d.items || [])).catch(() => setEvents([]))
    api.tickerBars(symbol, 365).then((d) => setBars(d.items || [])).catch(() => setBars([]))
    api.tickerEvents(symbol).then((d) => setGovEvents(d.items || [])).catch(() => setGovEvents([]))
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

      {bars.length > 2 && (
        <div className="panel" style={{ marginTop: 16 }}>
          <h3>Price context · 1y</h3>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={bars} margin={{ top: 12, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="rgba(139,148,158,.16)" vertical={false} />
              <XAxis dataKey="date" minTickGap={28} tick={{ fill: 'var(--muted)', fontSize: 11 }} />
              <YAxis domain={['dataMin', 'dataMax']} tickFormatter={(v) => `$${Math.round(v)}`} tick={{ fill: 'var(--muted)', fontSize: 11 }} width={48} />
              <Tooltip contentStyle={{ background: 'var(--panel)', border: '1px solid var(--border)' }} formatter={(v) => [`$${Number(v).toFixed(2)}`, 'Close']} />
              <Line type="monotone" dataKey="close" stroke="var(--accent)" dot={false} strokeWidth={2} />
              {(data.items || []).slice(0, 40).map((t) => {
                const p = bars.find((b) => b.date >= t.disclosure_date)?.close
                if (!p || !t.disclosure_date) return null
                return <ReferenceDot key={t.id} x={t.disclosure_date} y={p} r={3} fill={t.transaction_type === 'sale' ? 'var(--sell)' : 'var(--buy)'} stroke="none" />
              })}
            </LineChart>
          </ResponsiveContainer>
          <p className="note">Dots mark public disclosure dates, not transaction dates.</p>
        </div>
      )}

      {events.length > 0 && (
        <div className="panel" style={{ marginTop: 16 }}>
          <h3>Policy context</h3>
          <div className="news-list">
            {events.map((e) => (
              <a key={e.id} href={e.url} target="_blank" rel="noopener noreferrer">
                {e.title}<span className="src"> · {e.member || e.sector || e.event_type}</span>
              </a>
            ))}
          </div>
          <p className="note">Nearby Congress.gov activity by members exposed to this ticker’s sector. Context, not causality.</p>
        </div>
      )}

      {govEvents.length > 0 && (
        <div className="panel" style={{ marginTop: 16 }}>
          <h3>SEC event context</h3>
          <div className="news-list">
            {govEvents.map((e) => (
              <a key={e.id} href={e.url} target="_blank" rel="noopener noreferrer">
                <span className="tag src">{e.form}</span> {e.title}<span className="src"> · {e.filed_at || ''}</span>
              </a>
            ))}
          </div>
        </div>
      )}

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
