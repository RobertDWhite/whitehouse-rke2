import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'

function timeAgo(iso) {
  if (!iso) return ''
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}

// Render plain-ish markdown: paragraphs + **bold**. Keeps deps minimal.
function renderMd(md) {
  if (!md) return null
  return md.split(/\n{2,}/).map((p, i) => (
    <p key={i} dangerouslySetInnerHTML={{ __html: p.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>') }} />
  ))
}

export default function AiInsights({ memberId = null, defaultWindow = 7 }) {
  const [win, setWin] = useState(defaultWindow)
  const [data, setData] = useState(undefined)

  useEffect(() => {
    setData(undefined)
    const p = memberId ? api.aiMemberSummary(memberId, win) : api.aiSummary(win)
    p.then(setData).catch(() => setData(null))
  }, [win, memberId])

  if (data === null) return null // error → hide
  const hasSummary = data && data.summary_md

  return (
    <div className="panel ai-panel">
      <div className="ai-head">
        <span className="ai-title">🤖 AI Insights</span>
        {!memberId && (
          <span className="ai-windows">
            {[7, 30].map((w) => (
              <button key={w} className={`btn-sm ${win === w ? 'active' : ''}`} onClick={() => setWin(w)}>
                {w}d
              </button>
            ))}
          </span>
        )}
        {data?.generated_at && <span className="muted ai-ts">updated {timeAgo(data.generated_at)} · {data.model}</span>}
      </div>

      {data === undefined ? (
        <p className="muted">Loading…</p>
      ) : !hasSummary ? (
        <p className="muted">No summary generated yet — the AI job runs daily.</p>
      ) : (
        <>
          <div className="ai-summary">{renderMd(data.summary_md)}</div>
          {data.observations?.length > 0 && (
            <ul className="ai-obs">
              {data.observations.map((o, i) => (
                <li key={i}>
                  <span>{o.text}</span>{' '}
                  {(o.tickers || []).map((tk) => (
                    <Link key={tk} className="tag src" to={`/tickers/${tk}`}>{tk}</Link>
                  ))}
                  {(o.member_ids || []).map((mid, j) => (
                    <Link key={mid} className="tag" to={`/members/${mid}`}>{(o.members || [])[j] || 'member'}</Link>
                  ))}
                </li>
              ))}
            </ul>
          )}
          <p className="note ai-disclaimer">{data.disclaimer}</p>
        </>
      )}
    </div>
  )
}
