import { api, amountRange, signalLabel } from '../api.js'
import { useEffect, useState } from 'react'

function Field({ label, value }) {
  return (
    <div className="prov-field">
      <div className="label">{label}</div>
      <div>{value ?? '—'}</div>
    </div>
  )
}

export default function TradeProvenance({ tradeId, onClose }) {
  const [trade, setTrade] = useState(undefined)

  useEffect(() => {
    setTrade(undefined)
    api.trade(tradeId).then(setTrade).catch(() => setTrade(null))
  }, [tradeId])

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Trade Provenance</h2>
          <button className="btn-sm" onClick={onClose}>Close</button>
        </div>
        {trade === undefined ? <div className="loading">Loading…</div>
          : trade === null ? <p className="muted">Couldn’t load trade details.</p>
          : (
            <>
              <div className="prov-title">
                <strong>{trade.member || 'Unknown'}</strong> {trade.transaction_type} {trade.ticker || trade.asset_name || 'asset'} <span className="muted">{amountRange(trade)}</span>
              </div>
              <div className="prov-grid">
                <Field label="Source" value={trade.source} />
                <Field label="Primary" value={trade.provenance?.primary_source ? 'yes' : 'no'} />
                <Field label="Source priority" value={trade.provenance?.source_priority} />
                <Field label="Created" value={trade.provenance?.created_at} />
                <Field label="Traded" value={trade.transaction_date} />
                <Field label="Disclosed" value={trade.disclosure_date} />
                <Field label="Lag" value={trade.disclosure_lag_days != null ? `${trade.disclosure_lag_days} days` : null} />
                <Field label="Dedup key" value={trade.provenance?.dedup_key} />
              </div>

              <h3>Filing</h3>
              {trade.filing ? (
                <>
                  <div className="prov-grid">
                    <Field label="Doc ID" value={trade.filing.doc_id} />
                    <Field label="Parse status" value={trade.filing.parse_status} />
                    <Field label="Fetched" value={trade.filing.fetched_at} />
                    <Field label="Filing date" value={trade.filing.filing_date} />
                  </div>
                  {trade.filing.source_url && <a className="tag src" href={trade.filing.source_url} target="_blank" rel="noreferrer">Open source filing</a>}
                  {trade.filing.raw_excerpt && <pre className="raw-excerpt">{trade.filing.raw_excerpt}</pre>}
                </>
              ) : <p className="muted">No primary filing attached.</p>}

              <h3>Signals</h3>
              {trade.signals?.length ? (
                <div>
                  {trade.signals.map((s) => (
                    <span key={s.type} className={`tag sig sig-${s.type}`} title={s.detail ? JSON.stringify(s.detail) : ''}>{signalLabel(s.type)} · {s.score}</span>
                  ))}
                </div>
              ) : <p className="muted">No signal badges.</p>}

              <h3>Reconciliation</h3>
              {trade.reconciliation?.length ? trade.reconciliation.map((r, i) => (
                <div className="recon-note" key={i}>
                  <span className={`tag ${r.severity >= 3 ? 'sell' : 'exch'}`}>{r.kind}</span>
                  <span className="muted"> {r.comparison_source || ''} {r.created_at || ''}</span>
                  <pre>{JSON.stringify(r.detail || {}, null, 2)}</pre>
                </div>
              )) : <p className="muted">No reconciliation issues linked to this row.</p>}
            </>
          )}
      </div>
    </div>
  )
}
