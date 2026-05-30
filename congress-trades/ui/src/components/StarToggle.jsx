import { useWatchlist } from '../watchctx.jsx'

export default function StarToggle({ kind, value, label }) {
  const { has, toggle } = useWatchlist()
  const on = has(kind, String(value))
  return (
    <button
      className={`star ${on ? 'on' : ''}`}
      title={on ? 'Remove from watchlist' : `Watch ${label || value}`}
      onClick={(e) => { e.preventDefault(); e.stopPropagation(); toggle(kind, String(value)) }}
    >
      {on ? '★' : '☆'}
    </button>
  )
}
