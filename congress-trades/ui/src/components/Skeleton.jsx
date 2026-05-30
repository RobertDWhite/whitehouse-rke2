export function SkeletonCards({ n = 4 }) {
  return (
    <div className="cards">
      {Array.from({ length: n }).map((_, i) => (
        <div className="card" key={i}>
          <span className="skel" style={{ width: '60%' }} />
          <span className="skel" style={{ width: '40%', height: 26, marginTop: 10 }} />
        </div>
      ))}
    </div>
  )
}

export function SkeletonTable({ rows = 8 }) {
  return (
    <div className="panel" style={{ padding: 0 }}>
      {Array.from({ length: rows }).map((_, i) => (
        <div className="skel skel-row" key={i} />
      ))}
    </div>
  )
}

export function Empty({ glyph = '∅', children }) {
  return (
    <div className="empty">
      <span className="glyph">{glyph}</span>
      {children}
    </div>
  )
}
