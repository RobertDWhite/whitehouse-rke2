import React, { useEffect, useState } from 'react'
import { listApps, listReviews, postResponse, deleteResponse, draftReply } from './api.js'

function Stars({ n }) {
  return <span className="stars">{'★'.repeat(n)}{'☆'.repeat(5 - n)}</span>
}

function ReviewCard({ review, onChanged }) {
  const [draft, setDraft] = useState(review.response?.body || '')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')

  const run = async (label, fn) => {
    setBusy(label)
    setErr('')
    try {
      await fn()
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy('')
    }
  }

  const onDraft = () =>
    run('draft', async () => {
      const { draft: text } = await draftReply(review)
      setDraft(text)
    })

  const onPost = () =>
    run('post', async () => {
      await postResponse(review.id, draft)
      onChanged()
    })

  const onDelete = () =>
    run('delete', async () => {
      await deleteResponse(review.id)
      setDraft('')
      onChanged()
    })

  return (
    <div className="card">
      <div className="card-head">
        <Stars n={review.rating} />
        <span className="title">{review.title}</span>
        <span className="meta">
          {review.reviewer} · {review.territory} · {new Date(review.createdDate).toLocaleDateString()}
        </span>
      </div>
      <p className="body">{review.body}</p>

      {review.response && (
        <div className="existing">
          <span className="badge">{review.response.state === 'PUBLISHED' ? 'Published reply' : `Reply: ${review.response.state}`}</span>
          {review.response.lastModifiedDate && (
            <span className="meta"> · {new Date(review.response.lastModifiedDate).toLocaleDateString()}</span>
          )}
        </div>
      )}

      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder="Write a reply…"
        rows={4}
      />
      {err && <div className="err">{err}</div>}
      <div className="actions">
        <button onClick={onDraft} disabled={!!busy}>
          {busy === 'draft' ? 'Drafting…' : 'Draft reply'}
        </button>
        <button className="primary" onClick={onPost} disabled={!!busy || !draft.trim()}>
          {busy === 'post' ? 'Posting…' : review.response ? 'Update reply' : 'Post reply'}
        </button>
        {review.response && (
          <button className="danger" onClick={onDelete} disabled={!!busy}>
            {busy === 'delete' ? 'Deleting…' : 'Delete reply'}
          </button>
        )}
      </div>
    </div>
  )
}

export default function App() {
  const [apps, setApps] = useState([])
  const [appId, setAppId] = useState('')
  const [reviews, setReviews] = useState([])
  const [next, setNext] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    listApps()
      .then((d) => {
        setApps(d.apps)
        if (d.apps.length) setAppId(d.apps[0].id)
      })
      .catch((e) => setError(e.message))
  }, [])

  const load = (id, cursor) => {
    setLoading(true)
    setError('')
    listReviews(id, cursor)
      .then((d) => {
        setReviews((prev) => (cursor ? [...prev, ...d.reviews] : d.reviews))
        setNext(d.next)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    if (appId) {
      setReviews([])
      setNext(null)
      load(appId, null)
    }
  }, [appId])

  return (
    <div className="app">
      <header>
        <h1>App Store Reviews</h1>
        <select value={appId} onChange={(e) => setAppId(e.target.value)}>
          {apps.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
      </header>

      {error && <div className="err banner">{error}</div>}

      <div className="list">
        {reviews.map((r) => (
          <ReviewCard key={r.id} review={r} onChanged={() => load(appId, null)} />
        ))}
      </div>

      {!loading && !reviews.length && !error && <p className="empty">No reviews.</p>}
      {loading && <p className="empty">Loading…</p>}
      {next && !loading && (
        <button className="more" onClick={() => load(appId, next)}>
          Load more
        </button>
      )}
    </div>
  )
}
