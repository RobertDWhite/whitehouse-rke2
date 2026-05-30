import { convictionClass } from '../api.js'

export default function Conviction({ score }) {
  if (score == null) return <span className="muted">—</span>
  const cls = convictionClass(score)
  return (
    <span className={`conv ${cls}`} title="Disclosed conviction (lagged, informational — not a buy rating)">
      <span className="conv-bar"><span style={{ width: `${score}%` }} /></span>
      <span className="conv-num">{score}</span>
    </span>
  )
}
