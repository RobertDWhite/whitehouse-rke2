import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'
import TradeTable from '../components/TradeTable.jsx'
import { Empty, SkeletonTable } from '../components/Skeleton.jsx'
import { useWatchlist } from '../watchctx.jsx'

export default function Watchlist() {
  const { items, toggle } = useWatchlist()
  const [feed, setFeed] = useState(undefined)

  useEffect(() => { api.watchlistFeed().then((d) => setFeed(d.items || [])).catch(() => setFeed([])) }, [items.length])

  return (
    <>
      <h1>Watchlist</h1>
      {items.length === 0 ? (
        <Empty glyph="☆">Star a member or ticker anywhere in the app to track their trades here.</Empty>
      ) : (
        <>
          <div className="panel" style={{ marginBottom: 20 }}>
            {items.map((w) => (
              <span key={w.id} className="chip" style={{ fontSize: 13 }}>
                {w.kind === 'member' ? (
                  <Link to={`/members/${w.value}`}>{w.label}</Link>
                ) : (
                  <Link to={`/tickers/${w.value}`}>{w.value}</Link>
                )}{' '}
                <button className="star on" style={{ fontSize: 12 }} onClick={() => toggle(w.kind, w.value)} title="Remove">✕</button>
              </span>
            ))}
          </div>
          <h2>Recent trades from your watchlist</h2>
          {feed === undefined ? <SkeletonTable rows={8} /> : feed.length === 0 ? <Empty>No trades yet for these.</Empty> : <TradeTable items={feed} />}
        </>
      )}
    </>
  )
}
