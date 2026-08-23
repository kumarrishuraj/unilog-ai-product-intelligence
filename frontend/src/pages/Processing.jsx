import React, { useEffect, useState } from 'react'
import { api, num, pct } from '../lib/api'
import { Card, Empty, ErrorBox, Meter, Stat } from '../components/ui'

const STAGE_LABELS = {
  input_analysis: 'Input analysis',
  cleaning: 'Data cleaning & placeholder detection',
  entity_resolution: 'Manufacturer & brand resolution',
  classification: 'Product classification',
  attribute_extraction: 'Attribute extraction',
  normalization: 'LOV / UOM / fraction normalization',
  manufacturer_research: 'Manufacturer research',
  description_generation: 'Description generation',
  digital_assets: 'Digital asset discovery',
  validation: 'Validation',
  confidence_scoring: 'Confidence scoring',
  review_queue: 'Human review queue',
}

export default function Processing({ jobId, onNavigate }) {
  const [job, setJob] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!jobId) return
    let alive = true
    const tick = async () => {
      try {
        const j = await api.job(jobId)
        if (!alive) return
        setJob(j)
        if (j.status === 'running' || j.status === 'queued') setTimeout(tick, 400)
      } catch (e) { if (alive) setError(e.message) }
    }
    tick()
    return () => { alive = false }
  }, [jobId])

  if (!jobId) return <Empty title="No active run" hint="Start one from the Upload page." />
  if (error) return <ErrorBox error={error} />
  if (!job) return <Empty title="Loading run" />

  const running = job.status === 'running' || job.status === 'queued'
  const stats = job.stats

  return (
    <div className="space-y-4 max-w-5xl">
      <Card
        title={`Run ${job.id}`}
        subtitle={`${job.filename} — ${job.status}${job.phase ? ` (${job.phase})` : ''}`}
        right={
          job.status === 'done' && (
            <div className="flex gap-2">
              <a className="btn" href={api.exportUrl(job.id, 'csv')}>CSV</a>
              <a className="btn" href={api.exportUrl(job.id, 'xlsx')}>XLSX</a>
              <button className="btn btn-primary" onClick={() => onNavigate('dashboard')}>
                View dashboard
              </button>
            </div>
          )
        }
      >
        <Meter value={job.progress} tone={job.status === 'error' ? 'bad' : 'accent'}
          label={`${num(job.processed)} / ${num(job.total)} rows — ${job.elapsed}s`} />
        {job.error && <p className="text-sm text-bad mt-2">{job.error}</p>}
      </Card>

      <Card title="Pipeline stages"
        subtitle="Each stage is independently toggleable; disabled stages are omitted here.">
        <ol className="space-y-1.5">
          {(job.stages || []).map((s) => {
            const state = s.status
            const icon = state === 'done' ? '✓' : state === 'running' ? '◐' : '○'
            const cls = state === 'done' ? 'text-good'
              : state === 'running' ? 'text-accent animate-pulse' : 'text-slate-600'
            return (
              <li key={s.name}
                className="flex items-center gap-3 py-1.5 border-b border-white/5 last:border-0">
                <span className={`w-5 text-center font-mono ${cls}`}>{icon}</span>
                <span className={state === 'pending' ? 'text-slate-500' : 'text-slate-200'}>
                  {STAGE_LABELS[s.name] || s.name}
                </span>
              </li>
            )
          })}
        </ol>
      </Card>

      {stats && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <Stat label="Success" value={num(stats.success)} tone="good" />
            <Stat label="Partial" value={num(stats.partial)} tone="warn" />
            <Stat label="Needs review" value={num(stats.needs_review)} tone="warn" />
            <Stat label="Failed" value={num(stats.failed)}
              tone={stats.failed ? 'bad' : 'default'} />
          </div>
          <Card title="Performance">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 text-sm">
              <div>
                <div className="text-xs text-slate-400">Elapsed</div>
                <div className="text-lg tabular-nums">{stats.seconds}s</div>
              </div>
              <div>
                <div className="text-xs text-slate-400">Throughput</div>
                <div className="text-lg tabular-nums">{stats.throughput_per_sec}/s</div>
              </div>
              <div>
                <div className="text-xs text-slate-400">Extraction cache entries</div>
                <div className="text-lg tabular-nums">{num(stats.cache_hits)}</div>
              </div>
              <div>
                <div className="text-xs text-slate-400">Output columns</div>
                <div className="text-lg tabular-nums">{num(job.schema?.column_count)}</div>
              </div>
            </div>
          </Card>
        </>
      )}

      {job.profile_warnings?.length > 0 && (
        <Card title="Input warnings">
          <ul className="space-y-1">
            {job.profile_warnings.map((w, i) => (
              <li key={i} className="text-sm text-warn/90">• {w}</li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}
