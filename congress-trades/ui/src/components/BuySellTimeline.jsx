import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

const TT = { background: '#161b22', border: '1px solid #30363d', borderRadius: 6, fontSize: 12 }

export default function BuySellTimeline({ data, height = 240 }) {
  if (!data || data.length === 0) return <p className="muted">No activity in range.</p>
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 6, right: 8, left: -12, bottom: 0 }}>
        <CartesianGrid stroke="#21262d" vertical={false} />
        <XAxis dataKey="week" stroke="#8b949e" fontSize={11} tickFormatter={(d) => (d || '').slice(5)} />
        <YAxis stroke="#8b949e" fontSize={11} />
        <Tooltip contentStyle={TT} />
        <Area type="monotone" dataKey="purchase" stackId="1" stroke="#3fb950" fill="rgba(63,185,80,.35)" isAnimationActive={false} name="buys" />
        <Area type="monotone" dataKey="sale" stackId="1" stroke="#f85149" fill="rgba(248,81,73,.30)" isAnimationActive={false} name="sells" />
        <Area type="monotone" dataKey="exchange" stackId="1" stroke="#d29922" fill="rgba(210,153,34,.25)" isAnimationActive={false} name="exch" />
      </AreaChart>
    </ResponsiveContainer>
  )
}

export function Sparkline({ data, dataKey = 'count', height = 40 }) {
  if (!data || data.length === 0) return null
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
        <Area type="monotone" dataKey={dataKey} stroke="#58a6ff" fill="rgba(88,166,255,.25)" isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  )
}
