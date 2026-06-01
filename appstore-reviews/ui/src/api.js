async function req(method, path, body) {
  const opts = { method, headers: {} }
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const resp = await fetch(`/api${path}`, opts)
  const text = await resp.text()
  const data = text ? JSON.parse(text) : {}
  if (!resp.ok) throw new Error(data.detail || `Request failed (${resp.status})`)
  return data
}

export const listApps = () => req('GET', '/apps')
export const listReviews = (appId, cursor) =>
  req('GET', `/apps/${appId}/reviews${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`)
export const postResponse = (reviewId, body) =>
  req('POST', `/reviews/${reviewId}/response`, { body })
export const deleteResponse = (reviewId) => req('DELETE', `/reviews/${reviewId}/response`)
export const draftReply = (review) => req('POST', `/reviews/${review.id}/draft`, review)
