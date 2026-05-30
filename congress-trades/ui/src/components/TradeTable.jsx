import { Link } from 'react-router-dom'
import { amountRange, pct, signalLabel, typeClass } from '../api.js'
import Conviction from './Conviction.jsx'
import PartyBadge from './PartyBadge.jsx'
import StarToggle from './StarToggle.jsx'

function SignalBadges({ signals }) {
  if (!signals || signals.length === 0) return null
  return (
    <>
      {signals.map((s) => (
        <span key={s.type} className={`tag sig sig-${s.type}`} title={s.detail ? JSON.stringify(s.detail) : ''}>
          {signalLabel(s.type)}
        </span>
      ))}
    </>
  )
}

export default function TradeTable({ items, showMember = true }) {
  if (!items || items.length === 0) return <p className="muted">No trades.</p>
  return (
    <div className="panel" style={{ padding: 0, overflowX: 'auto' }}>
      <table>
        <thead>
          <tr>
            <th>Traded</th>
            <th>Disclosed</th>
            {showMember && <th>Member</th>}
            <th>Ticker</th>
            <th>Type</th>
            <th className="right">Amount</th>
            <th>Conviction</th>
            <th className="right">Return*</th>
            <th>Signals</th>
            <th>Src</th>
          </tr>
        </thead>
        <tbody>
          {items.map((t) => (
            <tr key={t.id}>
              <td className="nowrap">{t.transaction_date || '—'}</td>
              <td className="nowrap muted">
                {t.disclosure_date || '—'}
                {t.disclosure_lag_days != null && (
                  <span className={`lag ${t.disclosure_lag_days >= 45 ? 'lag-late' : ''}`}> +{t.disclosure_lag_days}d</span>
                )}
              </td>
              {showMember && (
                <td className="nowrap">
                  {t.member_id ? <Link to={`/members/${t.member_id}`}>{t.member}</Link> : t.member || '—'}{' '}
                  <PartyBadge party={t.party} />
                  {t.member_id ? <StarToggle kind="member" value={t.member_id} label={t.member} /> : null}
                  {t.state ? <span className="muted"> {t.state}{t.district ? `-${t.district}` : ''}</span> : null}
                </td>
              )}
              <td className="nowrap">
                {t.ticker ? (
                  <>
                    <Link to={`/tickers/${t.ticker}`}>{t.ticker}</Link> <StarToggle kind="ticker" value={t.ticker} />
                  </>
                ) : t.asset_type ? (
                  <span className="muted" title={t.asset_name || 'No ticker'}>{t.asset_type}</span>
                ) : '—'}
                {t.est_shares ? <div className="muted" style={{ fontSize: 11 }}>~{t.est_shares.toLocaleString()} sh @ ${t.price}</div> : null}
              </td>
              <td><span className={`tag ${typeClass(t.transaction_type)}`}>{t.transaction_type}</span></td>
              <td className="right nowrap num">{amountRange(t)}</td>
              <td><Conviction score={t.conviction} /></td>
              <td className={`right num ${t.excess_pct != null ? (t.excess_pct >= 0 ? 'pos' : 'neg') : ''}`}>
                {t.return_pct != null ? pct(t.return_pct) : '—'}
              </td>
              <td className="nowrap"><SignalBadges signals={t.signals} /></td>
              <td className="nowrap">
                {t.source_url ? (
                  <a href={t.source_url} target="_blank" rel="noopener noreferrer" className="tag src" title="View filing">↗</a>
                ) : <span className="tag src" title={t.source}>{(t.source || '').slice(0, 1).toUpperCase()}</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
