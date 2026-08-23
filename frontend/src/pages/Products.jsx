import React, { useEffect, useState } from 'react'
import { api, num, pct } from '../lib/api'
import { BandChip, Card, Empty, ErrorBox, Spinner, StatusChip } from '../components/ui'

const PAGE = 25

export default function Products({ jobId, onOpenProduct }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [offset, setOffset] = useState(0)
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')

  useEffect(() => {
    if (!jobId) return
    let alive = true
    setData(null)
    api.products(jobId, { offset, limit: PAGE, q, status })
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(e.message))
    return () => { alive = false }
  }, [jobId, offset, q, status])

  if (!jobId) return <Empty title="No run yet" hint="Enrich a feed first." />
  if (error) return <ErrorBox error={error} />

  return (
    <div className="space-y-3">
      <Card>
        <div className="flex flex-wrap gap-3 items-center">
          <input
            value={q}
            onChange={(e) => { setQ(e.target.value); setOffset(0) }}
            placeholder="Search part number, description or brand…"
            className="flex-1 min-w-[240px] bg-ink-700 border border-white/10 rounded-lg
                       px-3 py-1.5 text-sm placeholder:text-slate-600"
          />
          <select value={status}
            onChange={(e) => { setStatus(e.target.value); setOffset(0) }}
            className="bg-ink-700 border border-white/10 rounded-lg px-3 py-1.5 text-sm">
            <option value="">All statuses</option>
            <option value="SUCCESS">Success</option>
            <option value="PARTIAL">Partial</option>
            <option value="NEEDS_REVIEW">Needs review</option>
            <option value="FAILED">Failed</option>
          </select>
          {data && (
            <span className="text-xs text-slate-400 tabular-nums">
              {num(data.total)} products
            </span>
          )}
        </div>
      </Card>

      {!data ? <Spinner /> : (
        <Card className="p-0 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-white/[0.03]">
                <tr>
                  <th className="th">Part number</th>
                  <th className="th">Description</th>
                  <th className="th">Brand</th>
                  <th className="th">Category</th>
                  <th className="th text-center">Attributes</th>
                  <th className="th text-center">Confidence</th>
                  <th className="th text-center">Status</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((p) => (
                  <tr key={p.row_index}
                    onClick={() => onOpenProduct(p.row_index)}
                    className="hover:bg-accent/[0.06] cursor-pointer">
                    <td className="td font-mono text-xs text-accent-soft whitespace-nowrap">
                      {p.mpn || '—'}
                    </td>
                    <td className="td max-w-sm truncate text-slate-300">{p.description}</td>
                    <td className="td whitespace-nowrap">
                      {p.brand || <span className="text-slate-600 italic text-xs">unresolved</span>}
                    </td>
                    <td className="td text-xs text-slate-400 max-w-[15rem] truncate">
                      {p.classpath?.split('>').pop() || '—'}
                    </td>
                    <td className="td text-center text-xs tabular-nums text-slate-400">
                      {p.attributes_filled}/{p.attributes_total}
                    </td>
                    <td className="td text-center whitespace-nowrap">
                      <span className="tabular-nums text-xs mr-2">{pct(p.confidence)}</span>
                      <BandChip band={p.band} />
                    </td>
                    <td className="td text-center"><StatusChip status={p.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center justify-between px-3 py-2 border-t border-white/5">
            <button className="btn" disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
            <span className="text-xs text-slate-400 tabular-nums">
              {offset + 1}–{Math.min(offset + PAGE, data.total)} of {num(data.total)}
            </span>
            <button className="btn" disabled={offset + PAGE >= data.total}
              onClick={() => setOffset(offset + PAGE)}>Next</button>
          </div>
        </Card>
      )}
    </div>
  )
}
