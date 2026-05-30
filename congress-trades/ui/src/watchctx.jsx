import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { api } from './api.js'

const Ctx = createContext({ items: [], has: () => false, toggle: () => {}, reload: () => {} })

export function WatchlistProvider({ children }) {
  const [items, setItems] = useState([])

  const reload = useCallback(() => {
    api.watchlist().then((d) => setItems(d.items || [])).catch(() => {})
  }, [])

  useEffect(() => { reload() }, [reload])

  const has = useCallback(
    (kind, value) => items.some((w) => w.kind === kind && w.value === value),
    [items],
  )

  const toggle = useCallback(
    async (kind, value) => {
      const existing = items.find((w) => w.kind === kind && w.value === value)
      try {
        if (existing) await api.watchRemove(existing.id)
        else await api.watchAdd(kind, value)
      } catch { /* ignore */ }
      reload()
    },
    [items, reload],
  )

  return <Ctx.Provider value={{ items, has, toggle, reload }}>{children}</Ctx.Provider>
}

export const useWatchlist = () => useContext(Ctx)
