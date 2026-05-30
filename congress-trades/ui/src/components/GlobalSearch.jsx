import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'

export default function GlobalSearch() {
  const [q, setQ] = useState('')
  const [res, setRes] = useState(null)
  const [open, setOpen] = useState(false)
  const nav = useNavigate()
  const box = useRef(null)

  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        box.current?.querySelector('input')?.focus()
      }
    }
    const onClick = (e) => { if (box.current && !box.current.contains(e.target)) setOpen(false) }
    window.addEventListener('keydown', onKey)
    window.addEventListener('click', onClick)
    return () => { window.removeEventListener('keydown', onKey); window.removeEventListener('click', onClick) }
  }, [])

  useEffect(() => {
    if (q.trim().length < 2) { setRes(null); return }
    let live = true
    Promise.all([api.members({ q, limit: 6 }), api.tickers({ limit: 300 })])
      .then(([m, t]) => {
        if (!live) return
        const up = q.toUpperCase()
        setRes({
          members: (m.items || []).slice(0, 6),
          tickers: (t.items || []).filter((x) => x.ticker?.includes(up)).slice(0, 6),
        })
        setOpen(true)
      })
      .catch(() => {})
    return () => { live = false }
  }, [q])

  const go = (path) => { setQ(''); setOpen(false); setRes(null); nav(path) }

  return (
    <div className="search-wrap" ref={box}>
      <input
        className="search-input"
        placeholder="Search members / tickers  (⌘K)"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onFocus={() => res && setOpen(true)}
      />
      {open && res && (res.members.length || res.tickers.length) ? (
        <div className="search-results">
          {res.members.length > 0 && <div className="sr-group">Members</div>}
          {res.members.map((m) => (
            <a key={`m${m.id}`} onClick={() => go(`/members/${m.id}`)}>{m.full_name} <span className="muted">{m.party?.[0] || ''} {m.state || ''}</span></a>
          ))}
          {res.tickers.length > 0 && <div className="sr-group">Tickers</div>}
          {res.tickers.map((t) => (
            <a key={`t${t.ticker}`} onClick={() => go(`/tickers/${t.ticker}`)}>{t.ticker} <span className="muted">{t.count} trades</span></a>
          ))}
        </div>
      ) : null}
    </div>
  )
}
