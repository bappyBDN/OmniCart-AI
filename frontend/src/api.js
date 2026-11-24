const BASE = import.meta.env.VITE_API_BASE || ''

export async function api(path, { token, method = 'GET', body, signal } = {}) {
  const response = await fetch(`${BASE}/api${path}`, {
    method, signal,
    headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (response.status === 204) return null
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(d => `${d.loc?.slice(1).join('.')}: ${d.msg}`).join('; ') : data.detail
    const error = new Error(detail || `Request failed (${response.status})`)
    error.status = response.status
    throw error
  }
  return data
}

export const currency = value => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value || 0)
