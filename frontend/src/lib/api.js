const BASE = import.meta.env.VITE_API_BASE || ''

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail || detail } catch { /* non-JSON error */ }
    throw new Error(`${res.status}: ${detail}`)
  }
  const type = res.headers.get('content-type') || ''
  return type.includes('application/json') ? res.json() : res.blob()
}

export const api = {
  health: () => request('/api/health'),
  config: () => request('/api/config'),
  setStage: (stage, enabled) =>
    request('/api/config/stage', { method: 'POST', body: JSON.stringify({ stage, enabled }) }),

  upload: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return request('/api/upload', { method: 'POST', body: fd })
  },
  process: (uploadId, limit = 0) =>
    request(`/api/process?upload_id=${encodeURIComponent(uploadId)}&limit=${limit}`,
      { method: 'POST' }),
  processSample: (limit = 200) =>
    request(`/api/process/sample?limit=${limit}`, { method: 'POST' }),

  jobs: () => request('/api/jobs'),
  job: (id) => request(`/api/jobs/${id}`),
  dashboard: (id) => request(`/api/jobs/${id}/dashboard`),
  products: (id, params = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== '' && v != null)
    ).toString()
    return request(`/api/jobs/${id}/products${q ? `?${q}` : ''}`)
  },
  product: (id, rowIndex) => request(`/api/jobs/${id}/products/${rowIndex}`),
  review: (id, params = {}) => {
    const q = new URLSearchParams(params).toString()
    return request(`/api/jobs/${id}/review${q ? `?${q}` : ''}`)
  },
  applyReview: (id, decision) =>
    request(`/api/jobs/${id}/review`, { method: 'POST', body: JSON.stringify(decision) }),
  evaluation: (id) => request(`/api/jobs/${id}/evaluation`),
  exportUrl: (id, fmt) => `${BASE}/api/jobs/${id}/export?fmt=${fmt}`,
}

export const pct = (v) => (v == null ? '--' : `${(v * 100).toFixed(1)}%`)
export const num = (v) => (v == null ? '--' : v.toLocaleString())

export const bandColor = (band) => ({
  HIGH: 'text-good border-good/40 bg-good/10',
  MEDIUM: 'text-warn border-warn/40 bg-warn/10',
  LOW: 'text-orange-400 border-orange-400/40 bg-orange-400/10',
  UNKNOWN: 'text-bad border-bad/40 bg-bad/10',
}[band] || 'text-mute border-white/10 bg-white/5')

export const statusColor = (status) => ({
  SUCCESS: 'text-good border-good/40 bg-good/10',
  PARTIAL: 'text-warn border-warn/40 bg-warn/10',
  NEEDS_REVIEW: 'text-orange-400 border-orange-400/40 bg-orange-400/10',
  FAILED: 'text-bad border-bad/40 bg-bad/10',
}[status] || 'text-mute border-white/10 bg-white/5')

export const tierLabel = (tier) => ({
  manufacturer_product_page: 'Manufacturer product page',
  manufacturer_documentation: 'Manufacturer document',
  manufacturer_site: 'Manufacturer site',
  manufacturer_catalog: 'Manufacturer catalog',
  master_data: 'Master data',
  controlled_vocabulary: 'Controlled vocabulary',
  deterministic_transform: 'Deterministic transform',
  input_feed: 'Input feed',
  distributor: 'Distributor (fallback)',
  unverified: 'Unverified',
}[tier] || tier)
