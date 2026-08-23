import React, { useEffect, useState } from 'react'
import {
  Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { api, num, pct } from '../lib/api'
import { Card, Empty, ErrorBox, Meter, Spinner, Stat } from '../components/ui'

const BAND_COLORS = { HIGH: '#34d399', MEDIUM: '#fbbf24', LOW: '#fb923c', UNKNOWN: '#f87171' }

export default function Dashboard({ jobId, onNavigate }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!jobId) return
    setData(null)
    api.dashboard(jobId).then(setData).catch((e) => setError(e.message))
  }, [jobId])

  if (!jobId) {
    return (
      <Empty
        title="No run yet"
        hint="Upload a feed or enrich the bundled 1,000-row sample to populate the dashboard."
      />
    )
  }
  if (error) return <ErrorBox error={error} />
  if (!data) return <Spinner label="Loading metrics" />

  const { totals, quality, confidence_bands: bands, reference } = data
  const bandData = Object.entries(bands || {}).map(([name, value]) => ({ name, value }))
  const catData = (data.top_categories || []).slice(0, 8).map(([name, value]) => ({
    name: name.split('>').pop() || 'Unclassified', value,
  }))
  const flagData = (data.top_review_reasons || []).slice(0, 6).map(([name, value]) => ({
    name, value,
  }))

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat label="Products processed" value={num(totals.processed)}
          sub={`${data.job?.stats?.throughput_per_sec ?? '--'} rows/sec`} />
        <Stat label="Successfully enriched" value={num(totals.enriched)}
          sub={pct(totals.enriched / Math.max(1, totals.processed))} tone="good" />
        <Stat label="Average confidence" value={pct(quality.mean_confidence)}
          sub="weighted across six components" tone="accent" />
        <Stat label="Needs human review" value={num(totals.needs_review)}
          sub={pct(quality.review_rate)} tone="warn" />
      </div>

      <div className="grid lg:grid-cols-3 gap-4">
        <Card title="Quality gates"
          subtitle="Measured on this run, not estimated"
          className="lg:col-span-1">
          <div className="space-y-3">
            <Meter label="LOV compliance" value={quality.lov_compliance} tone="good" />
            <Meter label="Character-limit compliance" value={quality.char_compliance} tone="good" />
            <Meter label="Validation pass rate" value={quality.validation_pass_rate} tone="accent" />
            <Meter label="Evidence coverage" value={quality.evidence_coverage} tone="accent" />
            <Meter label="Human review rate" value={quality.review_rate} tone="warn" />
          </div>
        </Card>

        <Card title="Confidence distribution"
          subtitle="Every product carries a band, never a bare score">
          <ResponsiveContainer width="100%" height={200}>
            <PieChart>
              <Pie data={bandData} dataKey="value" nameKey="name" innerRadius={48}
                outerRadius={78} paddingAngle={2} stroke="none">
                {bandData.map((d) => (
                  <Cell key={d.name} fill={BAND_COLORS[d.name] || '#64748b'} />
                ))}
              </Pie>
              <Tooltip contentStyle={tooltipStyle} />
            </PieChart>
          </ResponsiveContainer>
          <div className="flex flex-wrap gap-3 justify-center text-xs">
            {bandData.map((d) => (
              <span key={d.name} className="flex items-center gap-1.5 text-slate-400">
                <span className="w-2 h-2 rounded-full"
                  style={{ background: BAND_COLORS[d.name] || '#64748b' }} />
                {d.name} <span className="tabular-nums text-slate-300">{d.value}</span>
              </span>
            ))}
          </div>
        </Card>

        <Card title="Top categories" subtitle="Assigned classpath leaf">
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={catData} layout="vertical" margin={{ left: 8, right: 12 }}>
              <XAxis type="number" hide />
              <YAxis type="category" dataKey="name" width={128} tick={axisTick} axisLine={false}
                tickLine={false} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'rgba(255,255,255,.04)' }} />
              <Bar dataKey="value" fill="#5b8cff" radius={[0, 4, 4, 0]} barSize={13} />
            </BarChart>
          </ResponsiveContainer>
        </Card>
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <Card title="Why products need review"
          subtitle="Specific, actionable reasons — not a generic low-confidence bucket"
          right={<button className="btn" onClick={() => onNavigate('review')}>Open queue</button>}>
          {flagData.length === 0 ? (
            <p className="text-sm text-slate-500 py-6 text-center">Nothing flagged.</p>
          ) : (
            <ResponsiveContainer width="100%" height={210}>
              <BarChart data={flagData} layout="vertical" margin={{ left: 8, right: 12 }}>
                <XAxis type="number" hide />
                <YAxis type="category" dataKey="name" width={210} tick={axisTick}
                  axisLine={false} tickLine={false} />
                <Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'rgba(255,255,255,.04)' }} />
                <Bar dataKey="value" fill="#fbbf24" radius={[0, 4, 4, 0]} barSize={13} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card title="Reference data in use"
          subtitle="Provenance is reported, never overstated">
          <div className="space-y-1.5 text-sm">
            {Object.entries(reference?.sources || {}).map(([k, v]) => {
              const [tier, detail] = String(v).split(':')
              const tone = tier === 'official' ? 'text-good'
                : tier === 'computed' ? 'text-accent-soft'
                  : tier === 'derived' ? 'text-warn' : 'text-slate-400'
              return (
                <div key={k} className="flex gap-3 items-baseline border-b border-white/5 pb-1.5">
                  <span className="w-40 shrink-0 text-xs text-slate-400">
                    {k.replace(/_/g, ' ')}
                  </span>
                  <span className={`text-xs font-semibold uppercase ${tone}`}>{tier}</span>
                  <span className="text-xs text-slate-500 flex-1 truncate">{detail}</span>
                </div>
              )
            })}
          </div>
          <div className="grid grid-cols-3 gap-2 mt-3 text-center">
            {[['Manufacturers', reference?.manufacturers], ['Brands', reference?.brands],
              ['Leaf nodes', reference?.leaf_nodes], ['LOV values', reference?.lov_values],
              ['UOM entries', reference?.uom_entries], ['Fractions', reference?.fraction_entries],
            ].map(([label, value]) => (
              <div key={label} className="bg-white/5 rounded-lg py-2">
                <div className="text-base font-semibold tabular-nums">{num(value)}</div>
                <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
              </div>
            ))}
          </div>
          {(reference?.warnings || []).length > 0 && (
            <ul className="mt-3 space-y-1">
              {reference.warnings.map((w, i) => (
                <li key={i} className="text-[11px] text-warn/80 leading-snug">• {w}</li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  )
}

const tooltipStyle = {
  background: '#111834', border: '1px solid rgba(255,255,255,.1)',
  borderRadius: 8, fontSize: 12, color: '#e2e8f0',
}
const axisTick = { fill: '#94a3b8', fontSize: 11 }
