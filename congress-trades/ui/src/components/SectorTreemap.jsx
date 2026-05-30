import { useNavigate } from 'react-router-dom'
import { ResponsiveContainer, Tooltip, Treemap } from 'recharts'
import { compactMoney } from '../api.js'

const COLORS = {
  Technology: '#1f6feb', Financials: '#3fb950', Healthcare: '#db61a2', Energy: '#d29922',
  'Consumer Discretionary': '#a371f7', 'Consumer Staples': '#2da44e', Industrials: '#6e7681',
  Communications: '#58a6ff', Materials: '#bf8700', Utilities: '#1f6feb', 'Real Estate': '#cf222e',
  Agriculture: '#7ee787', Unknown: '#484f58', Other: '#484f58',
}

function Cell(props) {
  const { x, y, width, height, name } = props
  if (width < 4 || height < 4) return null
  return (
    <g>
      <rect x={x} y={y} width={width} height={height} fill={COLORS[name] || '#30363d'} stroke="#0d1117" />
      {width > 60 && height > 24 && (
        <text x={x + 6} y={y + 18} fill="#fff" fontSize={12} fontWeight={600}>{name}</text>
      )}
    </g>
  )
}

export default function SectorTreemap({ data, height = 280 }) {
  const nav = useNavigate()
  if (!data || data.length === 0) return <p className="muted">No sector data yet.</p>
  const td = data.map((d) => ({ name: d.sector, size: Math.max(d.volume, 1), count: d.count }))
  return (
    <ResponsiveContainer width="100%" height={height}>
      <Treemap data={td} dataKey="size" stroke="#0d1117" isAnimationActive={false}
        content={<Cell />} onClick={(n) => n?.name && nav(`/feed?sector=${encodeURIComponent(n.name)}`)}>
        <Tooltip
          contentStyle={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 6, fontSize: 12 }}
          formatter={(v, _n, p) => [`${compactMoney(v)} · ${p?.payload?.count || 0} trades`, p?.payload?.name]}
        />
      </Treemap>
    </ResponsiveContainer>
  )
}
