import { Link } from 'react-router-dom'
import { amountRange, typeClass } from '../api.js'
import PartyBadge from './PartyBadge.jsx'

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
            <th>Owner</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {items.map((t) => (
            <tr key={t.id}>
              <td className="nowrap">{t.transaction_date || '—'}</td>
              <td className="nowrap muted">{t.disclosure_date || '—'}</td>
              {showMember && (
                <td className="nowrap">
                  {t.member_id ? (
                    <Link to={`/members/${t.member_id}`}>{t.member}</Link>
                  ) : (
                    t.member || '—'
                  )}
                  {' '}
                  <PartyBadge party={t.party} />
                  {t.state ? <span className="muted"> {t.state}{t.district ? `-${t.district}` : ''}</span> : null}
                </td>
              )}
              <td className="nowrap">
                {t.ticker ? (
                  <Link to={`/tickers/${t.ticker}`}>{t.ticker}</Link>
                ) : t.asset_type ? (
                  <span className="muted" title="No ticker (non-equity)">{t.asset_type}</span>
                ) : (
                  '—'
                )}
              </td>
              <td>{t.asset_name || '—'}</td>
              <td><span className={`tag ${typeClass(t.transaction_type)}`}>{t.transaction_type}</span></td>
              <td className="right nowrap">{amountRange(t)}</td>
              <td className="muted">{t.owner || '—'}</td>
              <td><span className="tag src">{t.source}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
