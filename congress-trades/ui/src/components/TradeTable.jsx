import { Link } from 'react-router-dom'
import { amountRange, signalLabel, typeClass } from '../api.js'
import PartyBadge from './PartyBadge.jsx'

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
            <th>Asset</th>
            <th>Type</th>
            <th className="right">Amount</th>
            <th>Signals</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {items.map((t) => (
            <tr key={t.id}>
              <td className="nowrap">{t.transaction_date || '—'}</td>
              <td className="nowrap muted">
                {t.disclosure_date || '—'}
                {t.disclosure_lag_days != null && (
                  <span className={`lag ${t.disclosure_lag_days >= 40 ? 'lag-late' : ''}`}> +{t.disclosure_lag_days}d</span>
                )}
              </td>
              {showMember && (
                <td className="nowrap">
                  {t.member_id ? <Link to={`/members/${t.member_id}`}>{t.member}</Link> : t.member || '—'}{' '}
                  <PartyBadge party={t.party} />
                  {t.state ? <span className="muted"> {t.state}{t.district ? `-${t.district}` : ''}</span> : null}
                </td>
              )}
              <td className="nowrap">
                {t.ticker ? <Link to={`/tickers/${t.ticker}`}>{t.ticker}</Link>
                  : t.asset_type ? <span className="muted" title="No ticker (non-equity)">{t.asset_type}</span> : '—'}
              </td>
              <td>
                {t.asset_name || '—'}
                {t.est_shares ? <span className="muted"> · ~{t.est_shares.toLocaleString()} sh</span> : null}
              </td>
              <td><span className={`tag ${typeClass(t.transaction_type)}`}>{t.transaction_type}</span></td>
              <td className="right nowrap">{amountRange(t)}</td>
              <td className="nowrap"><SignalBadges signals={t.signals} /></td>
              <td className="nowrap">
                {t.source_url ? (
                  <a href={t.source_url} target="_blank" rel="noopener noreferrer" className="tag src" title="View original filing">
                    {t.source} ↗
                  </a>
                ) : (
                  <span className="tag src">{t.source}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
