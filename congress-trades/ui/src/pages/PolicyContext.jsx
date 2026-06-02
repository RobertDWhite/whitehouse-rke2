import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, eventLabel } from '../api.js'

const EVENT_TYPES = [
  { value: '', label: 'All activity' },
  { value: 'member_house_vote', label: 'Member votes' },
  { value: 'house_vote', label: 'House votes' },
  { value: 'hearing', label: 'Hearings' },
  { value: 'committee_meeting', label: 'Committee meetings' },
  { value: 'bill', label: 'Bills' },
]

export default function PolicyContext() {
  const [eventType, setEventType] = useState('')
  const [days, setDays] = useState(120)
  const [events, setEvents] = useState(undefined)

  useEffect(() => {
    setEvents(undefined)
    api.legislativeEvents({ event_type: eventType, days, limit: 200 })
      .then((d) => setEvents(d.items || []))
      .catch(() => setEvents(null))
  }, [eventType, days])

  return (
    <>
      <h1>Policy Context</h1>
      <p className="note">Filter Congress.gov activity that may explain sector or member context around disclosed trades. This is context only, not causality.</p>

      <div className="panel policy-filter-panel">
        <div className="event-filter-row">
          {EVENT_TYPES.map((t) => (
            <button
              key={t.value || 'all'}
              className={`btn-sm ${eventType === t.value ? 'active' : ''}`}
              onClick={() => setEventType(t.value)}
              type="button"
            >
              {t.label}
            </button>
          ))}
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Policy event lookback">
            <option value="30">30 days</option>
            <option value="90">90 days</option>
            <option value="120">120 days</option>
            <option value="365">1 year</option>
          </select>
        </div>
      </div>

      {events === undefined ? <div className="loading">Loading…</div>
        : events === null ? <p className="muted">Couldn’t load policy context.</p>
        : events.length === 0 ? <div className="empty"><span className="glyph">No events</span>Try a longer lookback or a broader event type.</div>
        : (
          <div className="panel policy-list">
            {events.map((e) => (
              <div key={e.id} className="policy-row">
                <div>
                  <a href={e.url} target="_blank" rel="noopener noreferrer">{e.title}</a>
                  <div className="muted">
                    <span className="tag src">{eventLabel(e.event_type)}</span>
                    {e.member_id && e.member ? <> · <Link to={`/members/${e.member_id}`}>{e.member}</Link></> : null}
                    {e.sector ? ` · ${e.sector}` : ''}
                    {e.committee ? ` · ${e.committee}` : ''}
                  </div>
                </div>
                <div className="num muted">{e.occurred_at ? e.occurred_at.slice(0, 10) : '—'}</div>
              </div>
            ))}
          </div>
        )}
    </>
  )
}
