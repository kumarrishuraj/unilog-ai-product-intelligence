import React, { useEffect, useState } from 'react'
import { api, num, pct, tierLabel } from '../lib/api'
import { BandChip, Card, Empty, ErrorBox, Spinner, StatusChip } from '../components/ui'

export default function Review({ jobId, onOpenProduct }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const [edits, setEdits] = useState({})

  const load = () => {
    if (!jobId) return
    api.review(jobId, { limit: 200 }).then(setData).catch((e) => setError(e.message))
  }
  useEffect(load, [jobId])

  if (!jobId) return <Empty title="No run yet" hint="Enrich a feed first." />
  if (error) return <ErrorBox error={error} />
  if (!data) return <Spinner label="Loading review queue" />
  if (!data.total) {
    return <Empty title="Review queue is empty"
      hint="Every product cleared the confidence and validation thresholds." />
  }

  const act = async (entry, action) => {
    const key = `${entry.row_index}:${entry.field}`
    setBusy(key)
    try {
      await api.applyReview(jobId, {
        row_index: entry.row_index,
        field: entry.field,
        action,
        value: action === 'override' ? (edits[key] ?? entry.suggested_value ?? '') : null,
        note: 'reviewed in dashboard',
      })
      load()
    } catch (e) { setError(e.message) } finally { setBusy(null) }
  }

  return (
    <div className="space-y-3">
      <Card>
        <div className="flex items-baseline gap-3">
          <span className="text-lg font-semibold">{num(data.total)}</span>
          <span className="text-sm text-slate-400">
            open items — each is one specific, actionable issue
          </span>
        </div>
      </Card>

      <Card className="p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-white/[0.03]">
              <tr>
                <th className="th">Product</th>
                <th className="th">Issue</th>
                <th className="th">Detail</th>
                <th className="th">Evidence</th>
                <th className="th text-center">Confidence</th>
                <th className="th">Resolve</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((e) => {
                const key = `${e.row_index}:${e.field}`
                return (
                  <tr key={key} className="hover:bg-white/[0.03] align-top">
                    <td className="td">
                      <button className="font-mono text-xs text-accent-soft hover:underline"
                        onClick={() => onOpenProduct(e.row_index)}>
                        {e.mpn || `row ${e.row_index}`}
                      </button>
                      <div className="text-[11px] text-slate-500 max-w-[16rem] truncate">
                        {e.description}
                      </div>
                      <div className="mt-1"><StatusChip status={e.status} /></div>
                    </td>
                    <td className="td">
                      <div className="text-sm text-slate-200">{e.reason}</div>
                      {e.field && (
                        <div className="text-[11px] text-slate-500 font-mono">{e.field}</div>
                      )}
                    </td>
                    <td className="td text-xs text-slate-400 max-w-sm">{e.detail || '—'}</td>
                    <td className="td">
                      <div className="flex flex-col gap-1">
                        {(e.evidence || []).slice(0, 2).map((ev, i) => (
                          <span key={i} className="chip border-white/10 bg-white/5 text-slate-300"
                            title={ev.snippet}>
                            {tierLabel(ev.tier)}
                          </span>
                        ))}
                        {(e.evidence || []).length === 0 && (
                          <span className="text-[11px] text-warn/70">none</span>
                        )}
                      </div>
                    </td>
                    <td className="td text-center whitespace-nowrap">
                      <div className="tabular-nums text-xs">{pct(e.confidence)}</div>
                      <BandChip band={e.band} />
                    </td>
                    <td className="td">
                      <div className="flex flex-col gap-1.5 min-w-[13rem]">
                        <input
                          value={edits[key] ?? e.suggested_value ?? ''}
                          placeholder="corrected value"
                          onChange={(ev) => setEdits({ ...edits, [key]: ev.target.value })}
                          className="bg-ink-700 border border-white/10 rounded px-2 py-1 text-xs"
                        />
                        <div className="flex gap-1.5">
                          <button className="btn btn-primary text-xs py-1"
                            disabled={busy === key}
                            onClick={() => act(e, 'override')}>Apply</button>
                          <button className="btn text-xs py-1"
                            disabled={busy === key}
                            onClick={() => act(e, 'approve')}>Accept as-is</button>
                        </div>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
