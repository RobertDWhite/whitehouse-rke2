import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, compactMoney, pct } from '../api.js'
import AiInsights from '../components/AiInsights.jsx'
import PartyBadge from '../components/PartyBadge.jsx'
import StarToggle from '../components/StarToggle.jsx'
import TradeTable from '../components/TradeTable.jsx'
import { Sparkline } from '../components/BuySellTimeline.jsx'
import { SkeletonCards } from '../components/Skeleton.jsx'

export default function MemberDetail() {
  const { id } = useParams()
  const [data, setData] = useState(null)
  const [events, setEvents] = useState([])

  useEffect(() => {
    setData(null)
    api.member(id).then(setData).catch(() => setData(false))
    api.legislativeEvents({ member_id: id, limit: 10 }).then((d) => setEvents(d.items || [])).catch(() => setEvents([]))
  }, [id])

  if (data === false) return <p className="muted">Couldn’t load member.</p>
  if (!data) return <SkeletonCards n={4} />
  const m = data.member

  return (
    <>
      <h1>{m.full_name} <PartyBadge party={m.party} /> <StarToggle kind="member" value={m.id} label={m.full_name} /></h1>
      <p className="muted">
        <span className={`chamber-${m.chamber}`}>{m.chamber}</span>
        {m.party ? ` · ${m.party}` : ''}{m.state ? ` · ${m.state}${m.district ? `-${m.district}` : ''}` : ''}
        {` · ${m.trade_count} trades`}
      </p>
      {m.committees?.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          {m.committees.map((c) => <span className="chip" key={c}>{c}</span>)}
        </div>
      )}

      <div className="cards">
        {m.net_worth_min != null && (
          <div className="card">
            <div className="label">Net worth (est.)</div>
            <div className="big num">{compactMoney((m.net_worth_min + m.net_worth_max) / 2)}</div>
            <div className="note" style={{ marginTop: 4 }}>{compactMoney(m.net_worth_min)}–{compactMoney(m.net_worth_max)} · {m.net_worth_year}</div>
          </div>
        )}
        <div className="card"><div className="label">Disclosed volume</div><div className="big num">{compactMoney(m.est_volume)}</div><div className="note">activity proxy</div></div>
        {m.wt_excess_pct != null && (
          <div className="card">
            <div className="label">Return vs SPY*</div>
            <div className={`big num ${m.wt_excess_pct >= 0 ? 'pos' : 'neg'}`}>{pct(m.wt_excess_pct)}</div>
            <div className="note">follower, since disclosure</div>
          </div>
        )}
        {m.avg_lag_days != null && (
          <div className="card">
            <div className="label">Disclosure timing</div>
            <div className="big num">{Math.round(m.avg_lag_days)}d</div>
            <div className="note">{Math.round((m.pct_late || 0) * 100)}% filed ≥45d late</div>
          </div>
        )}
      </div>

      {data.monthly_activity?.length > 1 && (
        <div className="panel" style={{ marginTop: 16 }}>
          <h3>Activity over time</h3>
          <Sparkline data={data.monthly_activity} height={60} />
        </div>
      )}

      <div className="grid-2">
        <div className="panel">
          <h3>Trade types</h3>
          {Object.entries(data.by_transaction_type || {}).map(([k, v]) => (
            <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0' }}>
              <span className={`tag ${{ purchase: 'buy', sale: 'sell', exchange: 'exch' }[k] || 'other'}`}>{k}</span>
              <span className="num">{v}</span>
            </div>
          ))}
        </div>
        <div className="panel">
          <h3>Sector mix</h3>
          {data.sector_mix?.length > 0 ? data.sector_mix.map((s) => {
            const max = Math.max(...data.sector_mix.map((x) => x.volume), 1)
            return (
              <div key={s.sector} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0' }}>
                <span style={{ width: 110 }} className="muted">{s.sector}</span>
                <div className="volbar" style={{ width: `${(s.volume / max) * 100}%` }} />
                <span className="num muted">{compactMoney(s.volume)}</span>
              </div>
            )
          }) : <p className="muted">No sector data.</p>}
        </div>
      </div>

      <AiInsights memberId={m.id} defaultWindow={30} />

      {events.length > 0 && (
        <>
          <h2>Recent policy context</h2>
          <div className="panel">
            <div className="news-list">
              {events.map((e) => (
                <a key={e.id} href={e.url} target="_blank" rel="noopener noreferrer">
                  {e.title}<span className="src"> · {e.event_type}{e.sector ? ` · ${e.sector}` : ''}</span>
                </a>
              ))}
            </div>
            <p className="note">Congress.gov activity near this member’s trading record. Context only, not causality.</p>
          </div>
        </>
      )}

      {data.top_tickers?.length > 0 && (
        <>
          <h2>Top tickers</h2>
          <div className="panel">
            {data.top_tickers.map((t) => (
              <Link key={t.ticker} className="tag src" style={{ marginRight: 8, marginBottom: 6, display: 'inline-block' }} to={`/tickers/${t.ticker}`}>{t.ticker} · {t.count}</Link>
            ))}
          </div>
        </>
      )}

      <h2>Trades</h2>
      <TradeTable items={data.trades} showMember={false} />
    </>
  )
}
